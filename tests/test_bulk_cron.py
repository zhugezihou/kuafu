"""
Core test for CronScheduler — add/remove/list, parse_schedule, start/stop, _run_loop, CronTask.
"""
import json
import os
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, call, ANY

import pytest


class TestParseSchedule:
    """Complete coverage for parse_schedule."""

    def test_parse_interval_seconds(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("30s")
        assert interval == 30
        assert stype == "interval"

    def test_parse_interval_minutes(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("15m")
        assert interval == 900
        assert stype == "interval"

    def test_parse_interval_hours(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("2h")
        assert interval == 7200
        assert stype == "interval"

    def test_parse_interval_days(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("1d")
        assert interval == 86400
        assert stype == "interval"

    def test_parse_interval_case_insensitive(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("10M")
        assert interval == 600
        assert stype == "interval"

    def test_parse_cron_daily(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("0 8 * * *")
        assert stype == "cron"
        # Should be some positive interval until 8:00 next day
        assert interval > 0

    def test_parse_cron_every_minute(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("* * * * *")
        assert interval == 60
        assert stype == "cron"

    def test_parse_cron_specific_time_past(self):
        """When the cron time has passed today, it schedules for tomorrow."""
        from core.cron_scheduler import parse_schedule
        from datetime import datetime, timedelta
        # Use a time that's definitely in the past
        interval, stype = parse_schedule("0 0 * * *")
        assert stype == "cron"
        # Should be ~86400s (24h)
        assert interval > 80000

    def test_parse_iso_once(self):
        from core.cron_scheduler import parse_schedule
        from datetime import datetime, timedelta
        future = (datetime.now() + timedelta(hours=1)).isoformat()
        interval, stype = parse_schedule(future)
        assert stype == "once"
        assert interval > 0

    def test_parse_iso_past(self):
        from core.cron_scheduler import parse_schedule
        from datetime import datetime, timedelta
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        interval, stype = parse_schedule(past)
        assert stype == "once"
        assert interval == 0

    def test_parse_fallback(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("garbage input")
        assert interval == 1800
        assert stype == "interval"

    def test_parse_empty_string(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("")
        assert interval == 1800
        assert stype == "interval"

    def test_parse_whitespace(self):
        from core.cron_scheduler import parse_schedule
        interval, stype = parse_schedule("  30m  ")
        assert interval == 1800
        assert stype == "interval"


class TestFormatNextRun:
    """Complete coverage for format_next_run."""

    def test_format_once(self):
        from core.cron_scheduler import format_next_run
        result = format_next_run(100, "once")
        assert result == "一次性"

    def test_format_interval_seconds(self):
        from core.cron_scheduler import format_next_run
        result = format_next_run(30, "interval")
        assert "30 秒" in result

    def test_format_interval_minutes(self):
        from core.cron_scheduler import format_next_run
        result = format_next_run(600, "interval")
        assert "10 分钟" in result

    def test_format_interval_hours(self):
        from core.cron_scheduler import format_next_run
        result = format_next_run(7200, "interval")
        assert "小时" in result

    def test_format_cron(self):
        from core.cron_scheduler import format_next_run
        result = format_next_run(3600, "cron")
        assert "秒" in result


class TestCronTask:
    """Complete coverage for CronTask."""

    def test_init(self):
        from core.cron_scheduler import CronTask
        task = CronTask(name="test", schedule="10m", task_text="do something")
        assert task.name == "test"
        assert task.schedule_raw == "10m"
        assert task.task_text == "do something"
        assert task.enabled is True
        assert task.output_mode == "file"
        assert task.run_count == 0
        assert task.last_run is None
        assert task.interval == 600
        assert task.schedule_type == "interval"
        assert task.next_run > time.time()

    def test_init_disabled(self):
        from core.cron_scheduler import CronTask
        task = CronTask(name="test", schedule="5m", task_text="x", enabled=False)
        assert task.enabled is False

    def test_init_custom_values(self):
        from core.cron_scheduler import CronTask
        task = CronTask(name="custom", schedule="1h", task_text="x",
                         enabled=False, output_mode="feishu",
                         run_count=5, last_run="2025-01-01", last_result="ok")
        assert task.run_count == 5
        assert task.last_run == "2025-01-01"
        assert task.last_result == "ok"
        assert task.output_mode == "feishu"

    def test_to_dict(self):
        from core.cron_scheduler import CronTask
        task = CronTask(name="my_task", schedule="30m", task_text="hello",
                         run_count=3, last_run="2025-01-01", last_result="done")
        d = task.to_dict()
        assert d["name"] == "my_task"
        assert d["schedule"] == "30m"
        assert d["task"] == "hello"
        assert d["run_count"] == 3
        assert d["last_run"] == "2025-01-01"
        assert d["last_result"] == "done"

    def test_to_dict_no_last_result(self):
        from core.cron_scheduler import CronTask
        task = CronTask(name="t", schedule="10m", task_text="x")
        d = task.to_dict()
        assert d["last_result"] == ""

    def test_repr(self):
        from core.cron_scheduler import CronTask
        task = CronTask(name="my_task", schedule="10m", task_text="x")
        r = repr(task)
        assert "my_task" in r
        assert "10m" in r


class TestCronScheduler:
    """Complete coverage for CronScheduler."""

    # ---- init ----
    def test_init_defaults(self):
        from core.cron_scheduler import CronScheduler
        with patch('core.cron_scheduler.Path') as MockPath:
            with patch.object(CronScheduler, '_load_config'):
                with patch.object(CronScheduler, '_load_state'):
                    scheduler = CronScheduler()
                    assert scheduler._tasks == []
                    assert scheduler._running is False
                    assert scheduler.on_task_run is None

    def test_init_with_on_task_run(self):
        from core.cron_scheduler import CronScheduler
        cb = MagicMock()
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler(on_task_run=cb)
                assert scheduler.on_task_run is cb

    def test_init_with_config_path(self):
        from core.cron_scheduler import CronScheduler, ROOT_DIR
        with patch.object(CronScheduler, '_load_config') as mock_load:
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler(config_path="/tmp/test_config.yaml")
                mock_load.assert_called_once()

    # ---- add_task / remove_task / get_tasks / get_task ----
    def test_add_task(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                with patch.object(scheduler, '_save_state'):
                    task = CronTask(name="t1", schedule="10m", task_text="x")
                    scheduler.add_task(task)
                    assert len(scheduler._tasks) == 1
                    assert scheduler._tasks[0] is task

    def test_remove_task_exists(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler._tasks = [CronTask(name="t1", schedule="10m", task_text="x"),
                                    CronTask(name="t2", schedule="20m", task_text="y")]
                with patch.object(scheduler, '_save_state'):
                    result = scheduler.remove_task("t1")
                    assert result is True
                    assert len(scheduler._tasks) == 1
                    assert scheduler._tasks[0].name == "t2"

    def test_remove_task_not_exists(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler._tasks = []
                result = scheduler.remove_task("nonexistent")
                assert result is False

    def test_get_tasks(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                t1 = CronTask(name="t1", schedule="10m", task_text="x")
                scheduler._tasks = [t1]
                tasks = scheduler.get_tasks()
                assert len(tasks) == 1
                assert tasks[0] is t1
                # Verify it returns a copy (not the same list ref)
                tasks.append(CronTask(name="t2", schedule="10m", task_text="y"))
                assert len(scheduler._tasks) == 1

    def test_get_task_exists(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                t1 = CronTask(name="find_me", schedule="10m", task_text="x")
                scheduler._tasks = [t1]
                result = scheduler.get_task("find_me")
                assert result is t1

    def test_get_task_not_exists(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                result = scheduler.get_task("nothing")
                assert result is None

    # ---- _load_config ----
    def test_load_config_yaml_success(self):
        from core.cron_scheduler import CronScheduler
        config_yaml = """
tasks:
  - name: test_task
    schedule: "10m"
    task: "do something"
    enabled: true
    output_mode: file
"""
        with patch.object(CronScheduler, '_load_state'):
            with patch('builtins.open', MagicMock()):
                with patch('pathlib.Path.read_text', return_value=config_yaml):
                    with patch('core.cron_scheduler.yaml') as mock_yaml:
                        mock_yaml.safe_load.return_value = {
                            "tasks": [{"name": "test_task", "schedule": "10m", "task": "do something"}]
                        }
                        scheduler = CronScheduler(config_path="/tmp/test.yaml")
                        assert len(scheduler._tasks) == 1
                        assert scheduler._tasks[0].name == "test_task"

    def test_load_config_no_tasks(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_state'):
            with patch('pathlib.Path.read_text', return_value="tasks: []"):
                with patch('core.cron_scheduler.yaml') as mock_yaml:
                    mock_yaml.safe_load.return_value = {"tasks": []}
                    scheduler = CronScheduler(config_path="/tmp/empty.yaml")
                    assert scheduler._tasks == []

    def test_load_config_missing_field(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_state'):
            with patch('pathlib.Path.read_text', return_value="tasks:\n  - name: test"):
                with patch('core.cron_scheduler.yaml') as mock_yaml:
                    mock_yaml.safe_load.return_value = {"tasks": [{"name": "test"}]}
                    scheduler = CronScheduler(config_path="/tmp/minimal.yaml")
                    assert len(scheduler._tasks) == 1
                    assert scheduler._tasks[0].schedule_raw == "30m"  # default

    def test_load_config_pyyaml_import_error(self):
        """When yaml import fails, fall back to simple parser."""
        from core.cron_scheduler import CronScheduler
        config_text = """tasks:
  - name: simple_task
    schedule: 5m
    task: echo hello
    enabled: true
"""
        with patch.object(CronScheduler, '_load_state'):
            with patch('builtins.open', MagicMock()):
                with patch('pathlib.Path.read_text', return_value=config_text):
                    # Make yaml import fail
                    import builtins
                    orig_import = builtins.__import__

                    def mock_import(name, *args, **kw):
                        if name == 'yaml':
                            raise ImportError("no yaml")
                        return orig_import(name, *args, **kw)

                    with patch('builtins.__import__', side_effect=mock_import):
                        scheduler = CronScheduler(config_path="/tmp/no_yaml.yaml")
                        assert len(scheduler._tasks) == 1

    def test_load_config_read_error(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_state'):
            with patch('pathlib.Path.read_text', side_effect=Exception("IO error")):
                scheduler = CronScheduler(config_path="/tmp/bad.yaml")
                assert scheduler._tasks == []

    # ---- _parse_simple_yaml ----
    def test_parse_simple_yaml(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_state'):
            scheduler = CronScheduler()
            text = """tasks:
  - name: task1
    schedule: 10m
    task: do it
    enabled: true
    output_mode: file
  - name: task2
    schedule: 1h
    task: do that
    enabled: false
"""
            scheduler._parse_simple_yaml(text, Path("/tmp/test.yaml"))
            assert len(scheduler._tasks) == 2
            assert scheduler._tasks[0].name == "task1"
            assert scheduler._tasks[0].enabled is True
            assert scheduler._tasks[1].name == "task2"
            assert scheduler._tasks[1].enabled is False

    # ---- _load_state / _save_state ----
    def test_save_state(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler._tasks = [CronTask(name="t1", schedule="10m", task_text="x",
                                              run_count=3, last_result="done")]
                with patch.object(scheduler._state_path, 'write_text') as mock_write:
                    with patch.object(scheduler._state_path, 'parent') as mock_parent:
                        mock_parent.mkdir = MagicMock()
                        scheduler._save_state()
                        mock_write.assert_called_once()
                        written = json.loads(mock_write.call_args[0][0])
                        assert "tasks" in written
                        assert written["tasks"]["t1"]["run_count"] == 3

    def test_save_state_exception(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                with patch.object(scheduler._state_path, 'parent') as mp:
                    mp.mkdir.side_effect = PermissionError("denied")
                    scheduler._save_state()  # should not raise

    def test_load_state(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state') as mock_real:
                # We bypass _load_state in init, test it directly
                scheduler = CronScheduler()
                scheduler._tasks = [CronTask(name="t1", schedule="10m", task_text="x")]
                state_data = json.dumps({"tasks": {"t1": {"run_count": 5, "last_run": "2025-01-01", "last_result": "ok"}}})
                with patch.object(scheduler._state_path, 'exists', return_value=True):
                    with patch.object(scheduler._state_path, 'read_text', return_value=state_data):
                        scheduler._load_state()
                        assert scheduler._tasks[0].run_count == 5
                        assert scheduler._tasks[0].last_run == "2025-01-01"
                        assert scheduler._tasks[0].last_result == "ok"

    def test_load_state_no_file(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                with patch.object(scheduler._state_path, 'exists', return_value=False):
                    scheduler._load_state()  # should not raise

    def test_load_state_corrupted(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                with patch.object(scheduler._state_path, 'exists', return_value=True):
                    with patch.object(scheduler._state_path, 'read_text', return_value="not json"):
                        scheduler._load_state()  # should not raise

    # ---- start / stop ----
    def test_start(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler.start()
                assert scheduler._running is True
                assert scheduler._thread is not None

    def test_start_already_running(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler._running = True
                with patch('threading.Thread') as mock_thread:
                    scheduler.start()
                    mock_thread.assert_not_called()

    def test_stop(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler._running = True
                with patch.object(scheduler, '_save_state'):
                    scheduler.stop()
                    assert scheduler._running is False

    # ---- _run_loop ----
    def test_run_loop_no_tasks(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler._running = True
                scheduler._running = False  # exit immediately
                scheduler._run_loop()

    def test_run_loop_executes_due_task(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                task = CronTask(name="immediate", schedule="0s", task_text="do it")
                task.next_run = time.time() - 1  # already due
                scheduler._tasks = [task]
                scheduler.on_task_run = MagicMock(return_value="success")

                # Run one cycle then stop
                run_count = [0]
                orig_sleep = time.sleep
                def mock_sleep(s):
                    run_count[0] += 1
                    scheduler._running = False

                with patch('time.sleep', side_effect=mock_sleep):
                    scheduler._running = True
                    with patch.object(scheduler, '_save_state'):
                        scheduler._run_loop()
                        scheduler.on_task_run.assert_called_once_with(task)
                        assert task.run_count == 1
                        assert task.last_result == "success"

    def test_run_loop_task_execution_error(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                task = CronTask(name="failing", schedule="0s", task_text="fail")
                task.next_run = time.time() - 1
                scheduler._tasks = [task]
                scheduler.on_task_run = MagicMock(side_effect=Exception("runtime error"))

                def mock_sleep(s):
                    scheduler._running = False

                with patch('time.sleep', side_effect=mock_sleep):
                    scheduler._running = True
                    scheduler._run_loop()
                    assert task.run_count == 1
                    assert "错误" in task.last_result

    def test_run_loop_no_callback(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                scheduler.on_task_run = None
                task = CronTask(name="no_cb", schedule="0s", task_text="x")
                task.next_run = time.time() - 1
                scheduler._tasks = [task]

                def mock_sleep(s):
                    scheduler._running = False

                with patch('time.sleep', side_effect=mock_sleep):
                    scheduler._running = True
                    scheduler._run_loop()
                    assert task.last_result == "(无回调)"

    def test_run_loop_output_mode_file(self):
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                task = CronTask(name="file_out", schedule="0s", task_text="x", output_mode="file")
                task.next_run = time.time() - 1
                scheduler._tasks = [task]
                scheduler.on_task_run = MagicMock(return_value="result content")

                def mock_sleep(s):
                    scheduler._running = False

                with patch('time.sleep', side_effect=mock_sleep):
                    with patch.object(scheduler, '_save_to_file') as mock_save:
                        scheduler._running = True
                        scheduler._run_loop()
                        mock_save.assert_called_once_with(task)

    def test_run_loop_output_mode_feishu_no_bot(self):
        """feishu mode without bot set should fall back to file save."""
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                task = CronTask(name="feishu_out", schedule="0s", task_text="x", output_mode="feishu")
                task.next_run = time.time() - 1
                scheduler._tasks = [task]
                scheduler.on_task_run = MagicMock(return_value="result")

                def mock_sleep(s):
                    scheduler._running = False

                with patch('time.sleep', side_effect=mock_sleep):
                    with patch.object(scheduler, '_save_to_file') as mock_save:
                        scheduler._running = True
                        scheduler._run_loop()
                        # No feishu bot, so only _save_to_file is called
                        mock_save.assert_called_once_with(task)

    def test_run_loop_stop_during_execution(self):
        """If _running becomes False during task execution, loop exits."""
        from core.cron_scheduler import CronScheduler, CronTask
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                task = CronTask(name="stop_test", schedule="0s", task_text="x")
                task.next_run = time.time() - 1
                scheduler._tasks = [task]
                scheduler.on_task_run = MagicMock(return_value="x")

                def mock_sleep(s):
                    scheduler._running = False

                with patch('time.sleep', side_effect=mock_sleep):
                    scheduler._running = True
                    scheduler._run_loop()

    # ---- _save_to_file ----
    def test_save_to_file(self):
        from core.cron_scheduler import CronScheduler, CronTask, ROOT_DIR
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                task = CronTask(name="my_save_task", schedule="10m", task_text="x",
                                 run_count=1, last_run="2025-01-01", last_result="done")
                with patch.object(ROOT_DIR, '__truediv__') as mock_div:
                    mock_out_dir = MagicMock()
                    mock_div.return_value = mock_out_dir
                    mock_out_dir.__truediv__.return_value = mock_out_dir
                    mock_out_dir.mkdir = MagicMock()
                    mock_out_dir.__truediv__().write_text = MagicMock()

                    scheduler._save_to_file(task)
                    mock_out_dir.mkdir.assert_called_once()

    # ---- set_feishu_bot ----
    def test_set_feishu_bot(self):
        from core.cron_scheduler import CronScheduler
        with patch.object(CronScheduler, '_load_config'):
            with patch.object(CronScheduler, '_load_state'):
                scheduler = CronScheduler()
                bot = MagicMock()
                scheduler.set_feishu_bot(bot)
                assert scheduler._feishu_bot is bot
