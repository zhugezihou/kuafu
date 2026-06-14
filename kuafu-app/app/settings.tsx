// 夸父 App — 设置页
import { useState, useEffect } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import { theme } from '../src/theme';
import { getStatus, switchModel, getBaseUrl, setBaseUrl, StatusResponse } from '../src/api/gateway';

export default function SettingsScreen() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [gatewayUrl, setGatewayUrl] = useState(getBaseUrl());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    loadStatus();
  }, []);

  async function loadStatus() {
    try {
      const st = await getStatus();
      setStatus(st);
    } catch {}
  }

  function saveUrl() {
    setBaseUrl(gatewayUrl);
    Alert.alert('已保存', `Gateway URL 已更新为 ${gatewayUrl}`);
  }

  async function handleSwitchModel(target: string) {
    try {
      const result = await switchModel(target);
      Alert.alert(result.success ? '已切换' : '失败', result.message);
      loadStatus();
    } catch (e: any) {
      Alert.alert('错误', e.message);
    }
  }

  return (
    <SafeAreaView style={styles.container}>
      {/* 顶栏 */}
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <Text style={styles.backText}>← 返回</Text>
        </TouchableOpacity>
        <Text style={styles.headerTitle}>设置</Text>
        <View style={{ width: 60 }} />
      </View>

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {/* Gateway 连接 */}
        <Text style={styles.sectionTitle}>Gateway 连接</Text>
        <View style={styles.card}>
          <Text style={styles.label}>服务地址</Text>
          <View style={styles.inputRow}>
            <TextInput
              style={styles.input}
              value={gatewayUrl}
              onChangeText={setGatewayUrl}
              placeholder="http://127.0.0.1:8080"
              placeholderTextColor={theme.text2}
              autoCapitalize="none"
              autoCorrect={false}
            />
            <TouchableOpacity style={styles.saveBtn} onPress={saveUrl}>
              <Text style={styles.saveBtnText}>保存</Text>
            </TouchableOpacity>
          </View>
          <Text style={styles.hint}>
            在 Termux 中启动夸父后，默认地址为 http://127.0.0.1:8080
          </Text>
        </View>

        {/* Agent 状态 */}
        {status && (
          <>
            <Text style={styles.sectionTitle}>Agent 状态</Text>
            <View style={styles.card}>
              <InfoRow label="版本" value={status.version || '—'} />
              <InfoRow label="模型" value={status.model || '—'} />
              <InfoRow label="后端" value={status.backend || '—'} />
              <InfoRow label="任务数" value={`${status.task_count || 0}`} />
            </View>
          </>
        )}

        {/* 模型切换 */}
        <Text style={styles.sectionTitle}>模型切换</Text>
        <View style={styles.card}>
          <TouchableOpacity
            style={styles.modelBtn}
            onPress={() => handleSwitchModel('cloud')}
          >
            <Text style={styles.modelBtnText}>🌤 云端 DeepSeek</Text>
          </TouchableOpacity>
          <Text style={styles.hint}>手机版推荐使用云端模式</Text>
        </View>

        {/* 关于 */}
        <Text style={styles.sectionTitle}>关于</Text>
        <View style={styles.card}>
          <InfoRow label="应用" value="夸父手机版" />
          <InfoRow label="架构" value="Expo React Native" />
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.infoRow}>
      <Text style={styles.infoLabel}>{label}</Text>
      <Text style={styles.infoValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 12,
    paddingVertical: 10,
    backgroundColor: theme.surface,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
  },
  backBtn: {
    padding: 4,
    width: 60,
  },
  backText: {
    color: theme.accent,
    fontSize: 14,
  },
  headerTitle: {
    color: theme.text,
    fontSize: 16,
    fontWeight: '600',
  },
  body: {
    flex: 1,
  },
  bodyContent: {
    padding: 16,
    gap: 20,
    paddingBottom: 40,
  },
  sectionTitle: {
    color: theme.text2,
    fontSize: 12,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 1,
    marginBottom: -12,
  },
  card: {
    backgroundColor: theme.surface,
    borderRadius: theme.radiusSm,
    borderWidth: 1,
    borderColor: theme.border,
    padding: 14,
    gap: 10,
  },
  label: {
    color: theme.text2,
    fontSize: 12,
  },
  inputRow: {
    flexDirection: 'row',
    gap: 8,
  },
  input: {
    flex: 1,
    backgroundColor: theme.surface2,
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 8,
    color: theme.text,
    fontSize: 14,
    borderWidth: 1,
    borderColor: theme.border,
  },
  saveBtn: {
    backgroundColor: theme.accent,
    borderRadius: 6,
    paddingHorizontal: 14,
    justifyContent: 'center',
  },
  saveBtnText: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '600',
  },
  hint: {
    color: theme.text2,
    fontSize: 11,
    lineHeight: 16,
  },
  infoRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 4,
    borderBottomWidth: 0.5,
    borderBottomColor: theme.border,
  },
  infoLabel: {
    color: theme.text2,
    fontSize: 13,
  },
  infoValue: {
    color: theme.text,
    fontSize: 13,
    fontWeight: '500',
  },
  modelBtn: {
    backgroundColor: theme.surface2,
    borderRadius: 8,
    paddingVertical: 12,
    paddingHorizontal: 16,
    alignItems: 'center',
  },
  modelBtnText: {
    color: theme.text,
    fontSize: 14,
    fontWeight: '500',
  },
});
