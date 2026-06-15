// 夸父 App — 手机专用工具集
// GPS定位 / 扫码 / 剪贴板 / 文件管理 / 通知推送

import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Platform } from 'react-native';
import * as Location from 'expo-location';
import * as Clipboard from 'expo-clipboard';
import * as FileSystem from 'expo-file-system';
import * as Notifications from 'expo-notifications';
import { CameraView } from 'expo-camera';

// ── 类型 ──

export interface PhoneToolResult {
  success: boolean;
  type: 'location' | 'barcode' | 'clipboard' | 'file' | 'notification';
  data?: any;
  error?: string;
}

// ── Hook ──

export function usePhoneTools() {
  const [location, setLocation] = useState<Location.LocationObject | null>(null);
  const [clipboardContent, setClipboardContent] = useState<string>('');
  const [notificationPermission, setNotificationPermission] = useState(false);

  // ── 初始化权限 ──

  useEffect(() => {
    checkPermissions();
  }, []);

  async function checkPermissions() {
    // 通知权限
    const notif = await Notifications.getPermissionsAsync();
    setNotificationPermission(notif.granted);
  }

  // ── 1. GPS 定位 ──

  const getLocation = useCallback(async (): Promise<PhoneToolResult> => {
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        return { success: false, type: 'location', error: '定位权限被拒绝' };
      }

      const loc = await Location.getCurrentPositionAsync({
        accuracy: Location.Accuracy.Balanced,
      });
      setLocation(loc);

      // 获取逆地理编码（地址）
      const geocode = await Location.reverseGeocodeAsync({
        latitude: loc.coords.latitude,
        longitude: loc.coords.longitude,
      });

      const address = geocode[0]
        ? [geocode[0].city, geocode[0].district, geocode[0].street]
            .filter(Boolean)
            .join(' ')
        : '';

      return {
        success: true,
        type: 'location',
        data: {
          latitude: loc.coords.latitude,
          longitude: loc.coords.longitude,
          accuracy: loc.coords.accuracy,
          altitude: loc.coords.altitude,
          address,
          timestamp: loc.timestamp,
        },
      };
    } catch (e: any) {
      return { success: false, type: 'location', error: e.message };
    }
  }, []);

  // ── 2. 扫码 (条形码/二维码) ──

  const [isScanning, setIsScanning] = useState(false);
  const [scannedData, setScannedData] = useState('');

  const startScanning = useCallback((): Promise<PhoneToolResult> => {
    return new Promise((resolve) => {
      setIsScanning(true);
      setScannedData('');

      // 扫码结果通过 onBarcodeScanned 回调返回
      // 调用方需要渲染 CameraView 并监听
      // 这里返回一个待定状态，实际扫描由 CameraView 组件处理
      resolve({
        success: true,
        type: 'barcode',
        data: { status: 'scanning' },
      });
    });
  }, []);

  const handleBarcodeScanned = useCallback(
    (data: string) => {
      setScannedData(data);
      setIsScanning(false);
      return {
        success: true,
        type: 'barcode',
        data: { value: data },
      } as PhoneToolResult;
    },
    [],
  );

  // ── 3. 剪贴板 ──

  const getClipboard = useCallback(async (): Promise<PhoneToolResult> => {
    try {
      const hasString = await Clipboard.hasStringAsync();
      if (!hasString) {
        return { success: false, type: 'clipboard', error: '剪贴板为空' };
      }
      const text = await Clipboard.getStringAsync();
      setClipboardContent(text);
      return { success: true, type: 'clipboard', data: { text } };
    } catch (e: any) {
      return { success: false, type: 'clipboard', error: e.message };
    }
  }, []);

  const setClipboard = useCallback(async (text: string): Promise<PhoneToolResult> => {
    try {
      await Clipboard.setStringAsync(text);
      return { success: true, type: 'clipboard', data: { text } };
    } catch (e: any) {
      return { success: false, type: 'clipboard', error: e.message };
    }
  }, []);

  // ── 4. 文件管理 ──

  const getFileInfo = useCallback(async (path: string): Promise<PhoneToolResult> => {
    try {
      const info = await FileSystem.getInfoAsync(path);
      if (!info.exists) {
        return { success: false, type: 'file', error: '文件不存在' };
      }
      return {
        success: true,
        type: 'file',
        data: {
          path: info.uri,
          size: 'size' in info ? info.size : undefined,
          exists: info.exists,
          isDirectory: info.isDirectory,
          modificationTime: info.modificationTime,
        },
      };
    } catch (e: any) {
      return { success: false, type: 'file', error: e.message };
    }
  }, []);

  const readDirectory = useCallback(async (dir: string): Promise<PhoneToolResult> => {
    try {
      const files = await FileSystem.readDirectoryAsync(dir);
      return {
        success: true,
        type: 'file',
        data: { directory: dir, files },
      };
    } catch (e: any) {
      return { success: false, type: 'file', error: e.message };
    }
  }, []);

  const readFileAsBase64 = useCallback(async (path: string): Promise<PhoneToolResult> => {
    try {
      const content = await FileSystem.readAsStringAsync(path, {
        encoding: FileSystem.EncodingType.Base64,
      });
      return {
        success: true,
        type: 'file',
        data: { path, base64: content.slice(0, 10000) }, // 限制大小
      };
    } catch (e: any) {
      return { success: false, type: 'file', error: e.message };
    }
  }, []);

  // ── 5. 通知推送 ──

  const sendNotification = useCallback(
    async (title: string, body: string, data?: Record<string, string>): Promise<PhoneToolResult> => {
      try {
        const { status } = await Notifications.requestPermissionsAsync();
        if (status !== 'granted') {
          return { success: false, type: 'notification', error: '通知权限被拒绝' };
        }
        setNotificationPermission(true);

        await Notifications.scheduleNotificationAsync({
          content: {
            title,
            body,
            data: data || {},
            sound: true,
          },
          trigger: null, // 立即发送
        });

        return { success: true, type: 'notification', data: { title, body } };
      } catch (e: any) {
        return { success: false, type: 'notification', error: e.message };
      }
    },
    [],
  );

  // ── 通用工具调用入口（供聊天界面使用） ──

  const callTool = useCallback(
    async (tool: string, ...args: any[]): Promise<PhoneToolResult> => {
      switch (tool) {
        case 'location':
        case 'gps':
          return getLocation();
        case 'clipboard':
        case 'clipboard_get':
          return getClipboard();
        case 'clipboard_set':
          return setClipboard(args[0] || '');
        case 'file_info':
          return getFileInfo(args[0] || '');
        case 'file_list':
          return readDirectory(args[0] || '');
        case 'file_read':
          return readFileAsBase64(args[0] || '');
        case 'notification':
          return sendNotification(args[0] || '桃林', args[1] || '');
        default:
          return { success: false, type: 'location', error: `未知工具: ${tool}` };
      }
    },
    [getLocation, getClipboard, setClipboard, getFileInfo, readDirectory, readFileAsBase64, sendNotification],
  );

  return {
    // 状态
    location,
    clipboardContent,
    notificationPermission,
    isScanning,
    scannedData,

    // GPS
    getLocation,

    // 扫码
    startScanning,
    handleBarcodeScanned,

    // 剪贴板
    getClipboard,
    setClipboard,

    // 文件
    getFileInfo,
    readDirectory,
    readFileAsBase64,

    // 通知
    sendNotification,

    // 通用入口
    callTool,
  };
}
