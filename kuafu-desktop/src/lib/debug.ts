/**
 * 夸父 Desktop 调试日志系统
 * 
 * 记录所有关键操作：引擎启动、HTTP 请求、Tauri 调用、错误等。
 * 通过 Ctrl+Shift+D 或状态栏按钮打开/关闭。
 */

let _logs: Array<{ time: string; level: string; msg: string }> = [];
let _maxLogs = 500;
let _enabled = true;

export type LogLevel = "info" | "warn" | "error" | "debug";

export function log(level: LogLevel, msg: string) {
  if (!_enabled) return;
  const time = new Date().toLocaleTimeString();
  _logs.push({ time, level, msg });
  if (_logs.length > _maxLogs) _logs.splice(0, _logs.length - _maxLogs);
  // 也输出到控制台
  const prefix = `[Kuafu ${level.toUpperCase()}]`;
  if (level === "error") console.error(prefix, msg);
  else if (level === "warn") console.warn(prefix, msg);
  else console.log(prefix, msg);
}

export function getLogs() {
  return _logs;
}

export function clearLogs() {
  _logs = [];
}

/**
 * 包装 fetch 调用，自动记录请求和响应
 */
export async function trackedFetch(
  url: string,
  options: RequestInit,
  label: string
): Promise<Response> {
  log("debug", `[HTTP] ${label}: ${options.method || "GET"} ${url}`);
  try {
    const resp = await fetch(url, options);
    log("info", `[HTTP] ${label}: ${resp.status} ${resp.statusText}`);
    return resp;
  } catch (e: any) {
    log("error", `[HTTP] ${label}: ${e.message || e}`);
    throw e;
  }
}

/**
 * 包装 Tauri invoke 调用
 */
export async function trackedInvoke(
  invokeFn: any,
  cmd: string,
  args?: any
): Promise<any> {
  log("debug", `[Tauri] invoke: ${cmd}${args ? " " + JSON.stringify(args).slice(0, 200) : ""}`);
  try {
    const result = await invokeFn(cmd, args);
    log("info", `[Tauri] ${cmd}: ok`);
    return result;
  } catch (e: any) {
    const msg = e.message || String(e);
    log("error", `[Tauri] ${cmd}: ${msg}`);
    throw e;
  }
}
