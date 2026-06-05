"""测试 core/whiteboard/whiteboard.py — 白板数据存储。"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open


class TestWhiteboard:
    """Whiteboard 测试。"""

    def _make_wb(self):
        from core.whiteboard.whiteboard import Whiteboard
        with patch("pathlib.Path.mkdir"):
            with patch("pathlib.Path.exists", return_value=True):
                return Whiteboard(work_dir=Path("/tmp/wb"))

    def test_init(self):
        wb = self._make_wb()
        assert wb._partitions is not None

    def test_read_empty(self):
        wb = self._make_wb()
        with patch.object(wb, '_read_partition', return_value=[]):
            assert wb.read("current_state") == []

    def test_read_partition_called(self):
        wb = self._make_wb()
        with patch.object(wb, '_read_partition', return_value=[{"_id": "1"}]):
            result = wb.read("current_state")
            assert len(result) == 1

    def test_write(self):
        wb = self._make_wb()
        data = [{"_id": "1"}]
        with patch.object(wb, '_write_partition') as mock_w:
            wb.write("current_state", data)
            mock_w.assert_called_once_with("current_state", data)

    def test_append(self):
        wb = self._make_wb()
        with patch.object(wb, '_read_partition', return_value=[]):
            with patch.object(wb, '_write_partition') as mock_w:
                wb.append("current_state", {"key": "val"})
                args = mock_w.call_args[0][1]
                assert len(args) == 1
                assert args[0]["key"] == "val"
                assert "_id" in args[0]

    def test_get_found(self):
        wb = self._make_wb()
        entry = {"_id": "abc", "content": "test"}
        with patch.object(wb, '_read_partition', return_value=[entry]):
            assert wb.get("current_state", "abc") == entry

    def test_get_not_found(self):
        wb = self._make_wb()
        with patch.object(wb, '_read_partition', return_value=[]):
            assert wb.get("current_state", "missing") is None

    def test_update_found(self):
        wb = self._make_wb()
        entry = {"_id": "abc", "content": "old", "status": "pending"}
        with patch.object(wb, '_read_partition', return_value=[entry]):
            with patch.object(wb, '_write_partition') as mock_w:
                result = wb.update("current_state", "abc", {"content": "new"})
                assert result is True
                data = mock_w.call_args[0][1]
                assert data[0]["content"] == "new"
                assert data[0]["status"] == "pending"

    def test_update_not_found(self):
        wb = self._make_wb()
        with patch.object(wb, '_read_partition', return_value=[{"_id": "abc"}]):
            assert wb.update("current_state", "xyz", {"k": "v"}) is False

    def test_remove_found(self):
        wb = self._make_wb()
        with patch.object(wb, '_read_partition', return_value=[{"_id": "abc"}, {"_id": "xyz"}]):
            with patch.object(wb, '_write_partition') as mock_w:
                assert wb.remove("current_state", "abc") is True
                assert len(mock_w.call_args[0][1]) == 1

    def test_remove_not_found(self):
        wb = self._make_wb()
        with patch.object(wb, '_read_partition', return_value=[]):
            assert wb.remove("current_state", "missing") is False

    def test_clear_all(self):
        wb = self._make_wb()
        with patch.object(wb, '_write_partition') as mock_w:
            with patch("pathlib.Path.unlink"):
                wb.clear()
                # clear calls _write_partition for each partition
                assert mock_w.call_count == len(wb._partitions)

    def test_clear_specific(self):
        wb = self._make_wb()
        with patch.object(wb, '_write_partition') as mock_w:
            wb.clear("current_state")
            mock_w.assert_called_once_with("current_state", [])

    def test_summary(self):
        wb = self._make_wb()
        mock_read = MagicMock(return_value=[{"_id": "1"}])
        with patch.object(wb, '_read_partition', mock_read):
            summary = wb.summary()
            assert "current_state" in summary
            assert summary["current_state"]["count"] == 1

    def test_total_entries(self):
        wb = self._make_wb()
        mock_read = MagicMock(return_value=[{"_id": "1"}, {"_id": "2"}])
        with patch.object(wb, '_read_partition', mock_read):
            # total_entries is a @property
            assert wb.total_entries == len(wb._partitions) * 2

    def test_sync(self):
        wb = self._make_wb()
        wb._cache = {"current_state": [{"_id": "1"}]}
        with patch.object(wb, '_write_partition') as mock_w:
            wb.sync()
            mock_w.assert_called_once_with("current_state", [{"_id": "1"}])

    def test_partition_path(self):
        wb = self._make_wb()
        p = wb._partition_path("current_state")
        assert "current_state.json" in str(p)

    def test_read_partition_file_exists(self):
        wb = self._make_wb()
        mock_data = json.dumps([{"_id": "1"}])
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_text", return_value=mock_data):
                result = wb._read_partition("current_state")
                assert len(result) == 1

    def test_read_partition_file_not_exists(self):
        wb = self._make_wb()
        with patch("pathlib.Path.exists", return_value=False):
            assert wb._read_partition("current_state") == []

    def test_read_partition_json_decode_error(self):
        """JSON 解析异常返回空列表。"""
        wb = self._make_wb()
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_text", return_value="not json!!!"):
                result = wb._read_partition("current_state")
                assert result == []

    def test_init_creates_partitions(self):
        """初始化时不存在分区文件则写入空数据。"""
        with patch("pathlib.Path.mkdir"):
            with patch("pathlib.Path.exists", return_value=False):
                with patch.object(Path, 'write_text'):
                    from core.whiteboard.whiteboard import Whiteboard
                    wb = Whiteboard(work_dir=Path("/tmp/wb_init"))
                    assert len(wb._partitions) > 0

    def test_read_partition_from_cache(self):
        wb = self._make_wb()
        wb._cache["current_state"] = [{"_id": "cached"}]
        result = wb._read_partition("current_state")
        assert result[0]["_id"] == "cached"

    def test_write_partition_updates_cache(self):
        wb = self._make_wb()
        data = [{"_id": "1"}]
        with patch.object(Path, 'write_text'):
            wb._write_partition("current_state", data)
            assert wb._cache["current_state"] == data

    def test_plan_summary_many_steps(self):
        """超过5步时显示总数。"""
        wb = self._make_wb()
        steps = [{"description": f"Step {i}", "status": "pending"} for i in range(10)]
        with patch.object(wb, '_read_partition', return_value=steps):
            summary = wb.plan_summary()
            assert "还有" in summary
            assert "5" in summary

    def test_recent_steps(self):
        wb = self._make_wb()
        with patch.object(wb, '_read_partition', return_value=[{"step": "s1"}, {"step": "s2"}, {"step": "s3"}]):
            recent = wb.recent_steps(n=2)
            assert len(recent) == 2
            assert recent[0]["step"] == "s2"

    def test_current_task(self):
        wb = self._make_wb()
        with patch.object(wb, '_read_partition', return_value=[{"task": "build"}, {"task": "test"}]):
            task = wb.current_task()
            assert task["task"] == "test"

    def test_current_task_none(self):
        wb = self._make_wb()
        with patch.object(wb, '_read_partition', return_value=[]):
            assert wb.current_task() is None

    def test_plan_summary_no_plan(self):
        wb = self._make_wb()
        with patch.object(wb, '_read_partition', return_value=[]):
            assert wb.plan_summary() == ""

    def test_plan_summary_with_steps(self):
        wb = self._make_wb()
        steps = [
            {"description": "Step one", "status": "done"},
            {"description": "Step two", "status": "pending"},
        ]
        with patch.object(wb, '_read_partition', return_value=steps):
            summary = wb.plan_summary()
            assert "Step one" in summary
            assert "done" in summary

    def test_checkpoint(self):
        wb = self._make_wb()
        with patch.object(wb, '_read_partition', return_value=[{"_id": "1"}]):
            with patch("json.dump"):
                ck = wb.checkpoint()
                assert "checkpoint_id" in ck
                assert len(ck["partitions"]) == len(wb._partitions)

    def test_restore_not_found(self):
        wb = self._make_wb()
        with patch("pathlib.Path.exists", return_value=False):
            assert wb.restore("ck_xxx") is False

    def test_restore_success(self):
        wb = self._make_wb()
        snap_data = {"current_state": [{"_id": "1"}]}
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_text", return_value=json.dumps(snap_data)):
                with patch.object(wb, '_write_partition'):
                    assert wb.restore("ck_xxx") is True
