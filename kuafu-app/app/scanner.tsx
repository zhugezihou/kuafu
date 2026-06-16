// 夸父 App — 扫码页面（二维码/条形码）
import { useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { theme } from '../src/theme';

export default function ScannerScreen() {
  const [permission, requestPermission] = useCameraPermissions();
  const [scanned, setScanned] = useState(false);

  if (!permission) {
    return (
      <SafeAreaView style={styles.container}>
        <Text style={styles.text}>请求相机权限...</Text>
      </SafeAreaView>
    );
  }

  if (!permission.granted) {
    return (
      <SafeAreaView style={styles.container}>
        <Text style={styles.text}>需要相机权限才能扫码</Text>
        <TouchableOpacity style={styles.btn} onPress={requestPermission}>
          <Text style={styles.btnText}>授予权限</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Text style={styles.backText}>返回</Text>
        </TouchableOpacity>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <CameraView
        style={StyleSheet.absoluteFill}
        barcodeScannerSettings={{ barcodeTypes: ['qr', 'pdf417', 'ean13', 'code128', 'aztec'] }}
        onBarcodeScanned={scanned ? undefined : (result: any) => {
          setScanned(true);
          const data = result.data || result.raw || '';
          router.back();
          // 通过路由参数传递扫码结果
          router.setParams({ scanResult: data });
        }}
      >
        <View style={styles.overlay}>
          <View style={styles.scanFrame}>
            <Text style={styles.hint}>将二维码放入框内</Text>
          </View>
        </View>
      </CameraView>

      <View style={styles.footer}>
        <TouchableOpacity
          style={styles.footerBtn}
          onPress={() => {
            setScanned(false);
          }}
        >
          <Text style={styles.footerBtnText}>重新扫码</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={styles.footerBtn}
          onPress={() => router.back()}
        >
          <Text style={styles.footerBtnText}>关闭</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#000',
  },
  text: {
    color: '#fff',
    fontSize: 16,
    textAlign: 'center',
    marginTop: 40,
  },
  btn: {
    backgroundColor: theme.accent,
    padding: 14,
    borderRadius: 8,
    margin: 20,
    alignItems: 'center',
  },
  btnText: {
    color: '#fff',
    fontWeight: '600',
    fontSize: 15,
  },
  backBtn: {
    alignItems: 'center',
    padding: 12,
  },
  backText: {
    color: '#fff',
    fontSize: 14,
  },
  overlay: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  scanFrame: {
    width: 250,
    height: 250,
    borderWidth: 2,
    borderColor: theme.accent,
    borderRadius: 16,
    justifyContent: 'flex-end',
    alignItems: 'center',
    backgroundColor: 'transparent',
  },
  hint: {
    color: '#fff',
    fontSize: 13,
    marginBottom: -28,
    backgroundColor: 'rgba(0,0,0,0.6)',
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderRadius: 4,
  },
  footer: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    paddingVertical: 20,
    backgroundColor: 'rgba(0,0,0,0.8)',
  },
  footerBtn: {
    paddingHorizontal: 30,
    paddingVertical: 10,
  },
  footerBtnText: {
    color: '#fff',
    fontSize: 15,
  },
});
