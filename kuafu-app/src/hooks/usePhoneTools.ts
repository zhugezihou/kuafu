// 夸父 App — 手机专用工具集
// GPS定位 / 扫码 / 剪贴板 / 文件管理 / 通知推送 / 语音 / 拍照

import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Platform } from 'react-native';
import * as Location from 'expo-location';
import * as Clipboard from 'expo-clipboard';
import * as FileSystem from 'expo-file-system';
import * as Notifications from 'expo-notifications';
import { CameraView } from 'expo-camera';
import {
  ExpoSpeechRecognitionModule,
  useSpeechRecognitionEvent,
} from '@jamsch/expo-speech-recognition';
import { getBaseUrl } from '../api/gateway';

// ── 类型 ──

export interface PhoneToolResult {
  success: boolean;
  type: 'location' | 'barcode' | 'clipboard' | 'file' | 'notification' | 'photo' | 'voice';
  data?: any;
  error?: string;
}

// ── Hook ──

export function usePhoneTools() {
  const [location, setLocation] = useState<Location.LocationObject | null>(null);
  const [clipboardContent, setClipboardContent] = useState<string>('');
  const [notificationPermission, setNotificationPermission] = useState(false);

  // 语音状态
  const [isRecording, setIsRecording] = useState(false);
  const [voiceText, setVoiceText] = useState('');

  // 拍照状态
  const [photoUri, setPhotoUri] = useState<string | null>(null);

  // 扫码状态
  const [isScanning, setIsScanning] = useState(false);
  const [scannedData, setScannedData] = useState('');

  // ── 初始化权限 ──

  useEffect(() => {
    checkPermissions();
  }, []);

  async function checkPermissions() {
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
      const geocode = await Location.reverseGeocodeAsync({
        latitude: loc.coords.latitude,
        longitude: loc.coords.longitude,
      });
      const address = geocode[0]
        ? [geocode[0].city, geocode[0].district, geocode[0].street].filter(Boolean).join(' ')
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

  // ── 2. 扫码 ──

  const startScanning = useCallback((): Promise<PhoneToolResult> => {
    setIsScanning(true);
    setScannedData('');
    return Promise.resolve({ success: true, type: 'barcode', data: { status: 'scanning' } });
  }, []);

  const handleBarcodeScanned = useCallback((data: string) => {
    setScannedData(data);
    setIsScanning(false);
    return { success: true, type: 'barcode', data: { value: data } } as PhoneToolResult;
  }, []);

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
        },
      };
    } catch (e: any) {
      return { success: false, type: 'file', error: e.message };
    }
  }, []);

  const readDirectory = useCallback(async (dir: string): Promise<PhoneToolResult> => {
    try {
      const files = await FileSystem.readDirectoryAsync(dir);
      return { success: true, type: 'file', data: { directory: dir, files } };
    } catch (e: any) {
      return { success: false, type: 'file', error: e.message };
    }
  }, []);

  const readFileAsBase64 = useCallback(async (path: string): Promise<PhoneToolResult> => {
    try {
      const content = await FileSystem.readAsStringAsync(path, {
        encoding: FileSystem.EncodingType.Base64,
      });
      return { success: true, type: 'file', data: { path, base64: content.slice(0, 10000) } };
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
          content: { title, body, data: data || {}, sound: true },
          trigger: null,
        });
        return { success: true, type: 'notification', data: { title, body } };
      } catch (e: any) {
        return { success: false, type: 'notification', error: e.message };
      }
    },
    [],
  );

  // ── 6. 语音输入 ──

  // 语音识别事件：中间结果
  useSpeechRecognitionEvent('result', (event) => {
    const transcript = event.results?.[0]?.transcript || '';
    if (event.isFinal) {
      setVoiceText(transcript);
      setIsRecording(false);
    } else {
      // 中间结果实时更新 UI
      setVoiceText(transcript);
    }
  });

  // 语音识别事件：错误
  useSpeechRecognitionEvent('error', (event) => {
    console.warn('语音识别错误:', event.error, event.message);
    setIsRecording(false);
    setVoiceText('');
  });

  // 语音识别事件：结束
  useSpeechRecognitionEvent('end', () => {
    setIsRecording(false);
  });

  const startVoiceInput = useCallback(async (): Promise<PhoneToolResult> => {
    try {
      const perm = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
      if (!perm.granted) {
        return { success: false, type: 'voice', error: '语音权限被拒绝' };
      }

      setIsRecording(true);
      setVoiceText('');

      ExpoSpeechRecognitionModule.start({
        lang: 'zh-CN',
        interimResults: true,
        continuous: false,
        addsPunctuation: true,
        maxAlternatives: 1,
      });

      // 等待最终结果
      // 事件监听会更新 voiceText 状态，调用方通过 voiceText 获取最终结果
      // 这里先返回一个中间状态
      return { success: true, type: 'voice', data: { text: '' } };
    } catch (e: any) {
      setIsRecording(false);
      return { success: false, type: 'voice', error: e.message };
    }
  }, []);

  // 停止语音识别
  const stopVoiceInput = useCallback(async (): Promise<PhoneToolResult> => {
    try {
      ExpoSpeechRecognitionModule.stop();
      // 等待一小段让 final result 事件触发
      await new Promise((r) => setTimeout(r, 500));
      const text = voiceText;
      setIsRecording(false);
      return { success: true, type: 'voice', data: { text: text || '' } };
    } catch (e: any) {
      return { success: false, type: 'voice', error: e.message };
    }
  }, [voiceText]);

  // ── 7. 拍照 ──

  const takePhoto = useCallback(async (): Promise<PhoneToolResult> => {
    try {
      // expo-image-picker 的 launchCameraAsync
      const ImagePicker = require('expo-image-picker');
      const result = await ImagePicker.launchCameraAsync({
        quality: 0.8,
        base64: false,
        exif: false,
      });
      if (!result.canceled && result.assets?.length > 0) {
        const uri = result.assets[0].uri;
        setPhotoUri(uri);
        return { success: true, type: 'photo', data: { uri } };
      }
      return { success: false, type: 'photo', error: '用户取消' };
    } catch (e: any) {
      return { success: false, type: 'photo', error: e.message };
    }
  }, []);

  // ── 发送工具结果到 Gateway ──

  const sendToolToGateway = useCallback(async (tool: string, data: any): Promise<boolean> => {
    try {
      const url = `${getBaseUrl()}/api/phone/tool`;
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tool, data }),
      });
      const result = await res.json();
      return result.success === true;
    } catch {
      return false;
    }
  }, []);

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
          return sendNotification(args[0] || '夸父', args[1] || '');
        case 'voice':
          return startVoiceInput();
        case 'photo':
          return takePhoto();
        default:
          return { success: false, type: 'location', error: `未知工具: ${tool}` };
      }
    },
    [getLocation, getClipboard, setClipboard, getFileInfo, readDirectory, readFileAsBase64,
     sendNotification, startVoiceInput, takePhoto],
  );

  return {
    // 状态
    location, clipboardContent, notificationPermission,
    isScanning, scannedData,
    isRecording, voiceText,
    photoUri,

    // GPS
    getLocation,
    // 扫码
    startScanning, handleBarcodeScanned,
    // 剪贴板
    getClipboard, setClipboard,
    // 文件
    getFileInfo, readDirectory, readFileAsBase64,
    // 通知
    sendNotification,
    // 语音
    startVoiceInput,
    stopVoiceInput,
    // 拍照
    takePhoto,
    // Gateway
    sendToolToGateway,
    // 通用入口
    callTool,
  };
}
