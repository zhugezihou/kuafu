// 夸父 App — 聊天主界面
import { useState, useRef, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  TextInput,
  FlatList,
  TouchableOpacity,
  StyleSheet,
  KeyboardAvoidingView,
  Platform,
  Animated,
  ActivityIndicator,
  Modal,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import { theme } from '../src/theme';
import { usePhoneTools } from '../src/hooks/usePhoneTools';
import {
  sendMessage,
  getStatus,
  resetConversation,
  approveRequest,
  rejectRequest,
  getPendingApprovals,
  setBaseUrl,
  StatusResponse,
  PendingApproval,
} from '../src/api/gateway';
import {
  loadMessages,
  saveMessages,
  appendMessage,
  updateMessage,
  clearMessages,
  StoredMessage,
} from '../src/store/storage';

// ── Markdown 渲染 ──
function parseMarkdown(text: string): { plain: string; segments: { text: string; bold?: boolean; code?: boolean; link?: string }[] } {
  const segments: { text: string; bold?: boolean; code?: boolean; link?: string }[] = [];
  const regex = /(\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: text.slice(lastIndex, match.index) });
    }
    if (match[2]) {
      segments.push({ text: match[2], bold: true });
    } else if (match[3]) {
      segments.push({ text: match[3], code: true });
    } else if (match[4] && match[5]) {
      segments.push({ text: match[4], link: match[5] });
    }
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < text.length) {
    segments.push({ text: text.slice(lastIndex) });
  }
  return { plain: text, segments };
}

function MarkdownText({ content }: { content: string }) {
  const { segments } = parseMarkdown(content);
  return (
    <Text style={styles.msgText}>
      {segments.map((seg, i) => {
        if (seg.code) {
          return <Text key={i} style={styles.inlineCode}>{seg.text}</Text>;
        }
        if (seg.bold) {
          return <Text key={i} style={{ fontWeight: '700' }}>{seg.text}</Text>;
        }
        if (seg.link) {
          return <Text key={i} style={styles.link}>{seg.text}</Text>;
        }
        return <Text key={i}>{seg.text}</Text>;
      })}
    </Text>
  );
}

export default function ChatScreen() {
  const [messages, setMessages] = useState<StoredMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [connecting, setConnecting] = useState(true);
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const [approvalModal, setApprovalModal] = useState(false);
  const [showToolbar, setShowToolbar] = useState(false);
  const flatListRef = useRef<FlatList>(null);
  const inputRef = useRef<TextInput>(null);
  const fadeAnim = useRef(new Animated.Value(0)).current;
  const phoneTools = usePhoneTools();
  const { getLocation, getClipboard, sendNotification, readDirectory } = phoneTools;

  // ── 初始化 ──
  useEffect(() => {
    initApp();
  }, []);

  async function initApp() {
    setConnecting(true);

    // 加载历史消息
    const saved = await loadMessages();
    if (saved.length > 0) {
      setMessages(saved);
    }

    // 尝试连接 Gateway
    for (let i = 0; i < 5; i++) {
      try {
        const st = await getStatus();
        setStatus(st);
        setConnecting(false);

        // 加载待审批
        loadApprovals();

        // 如果没有历史消息，加欢迎语
        if (saved.length === 0) {
          const welcome: StoredMessage = {
            id: 'welcome',
            role: 'assistant',
            content: `你好！我是夸父 v${st.version || '?'}，有什么可以帮你的？`,
            timestamp: Date.now(),
          };
          setMessages([welcome]);
          appendMessage(welcome);
        }

        Animated.timing(fadeAnim, {
          toValue: 1,
          duration: 300,
          useNativeDriver: true,
        }).start();

        // 定时刷新审批状态
        startApprovalPolling();
        return;
      } catch {
        await new Promise(r => setTimeout(r, 2000));
      }
    }
    setConnecting(false);
    if (saved.length === 0) {
      setMessages([{
        id: 'offline',
        role: 'assistant',
        content: '⚠️ 无法连接到夸父 Gateway。\n\n请确保已在 Termux 中启动夸父:\n`bash mobile/start-mobile.sh`\n\n设置页可修改 Gateway 地址。',
        timestamp: Date.now(),
      }]);
    }
  }

  // ── 审批轮询 ──
  function startApprovalPolling() {
    const timer = setInterval(loadApprovals, 5000);
    return () => clearInterval(timer);
  }

  async function loadApprovals() {
    try {
      const result = await getPendingApprovals();
      if (result.success) {
        setApprovals(result.approvals);
        if (result.approvals.length > 0) {
          setApprovalModal(true);
        }
      }
    } catch {}
  }

  async function handleApprove(reqId: string) {
    try {
      await approveRequest(reqId);
      loadApprovals();
    } catch {}
  }

  async function handleReject(reqId: string) {
    try {
      await rejectRequest(reqId);
      loadApprovals();
    } catch {}
  }

  // ── 发送消息 ──
  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || loading) return;

    setInput('');
    const userMsg: StoredMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: text,
      timestamp: Date.now(),
    };
    const assistantMsg: StoredMessage = {
      id: `assistant-${Date.now()}`,
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
    };

    setMessages(prev => [...prev, userMsg, assistantMsg]);
    appendMessage(userMsg);
    appendMessage(assistantMsg);
    setLoading(true);

    try {
      const result = await sendMessage(text);
      const reply = result.message || result.summary || (result.success ? '完成' : '出错');

      setMessages(prev =>
        prev.map(m => (m.id === assistantMsg.id ? { ...m, content: reply } : m))
      );
      updateMessage(assistantMsg.id, { content: reply });
    } catch (e: any) {
      const errMsg = `❌ 错误: ${e.message}`;
      setMessages(prev =>
        prev.map(m => (m.id === assistantMsg.id ? { ...m, content: errMsg } : m))
      );
      updateMessage(assistantMsg.id, { content: errMsg });
    }

    setLoading(false);
    setTimeout(() => flatListRef.current?.scrollToEnd({ animated: true }), 100);
  }, [input, loading]);

  // ── 重置对话 ──
  async function handleNewChat() {
    await clearMessages();
    setMessages([{
      id: 'welcome-' + Date.now(),
      role: 'assistant',
      content: '对话已重置。有什么可以帮你的？',
      timestamp: Date.now(),
    }]);
    try {
      await resetConversation();
    } catch {}
  }

  // ── 渲染消息 ──
  const renderMessage = useCallback(({ item }: { item: StoredMessage }) => {
    const isUser = item.role === 'user';
    const isStreaming = !item.content;
    return (
      <View style={[styles.msgRow, isUser && styles.msgRowUser]}>
        {!isUser && (
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>夸</Text>
          </View>
        )}
        <View style={[styles.msgBubble, isUser && styles.msgBubbleUser]}>
          {isStreaming ? (
            <ActivityIndicator color={theme.accent} size="small" />
          ) : (
            <MarkdownText content={item.content} />
          )}
        </View>
        {isUser && (
          <View style={[styles.avatar, styles.avatarUser]}>
            <Text style={[styles.avatarText, { color: theme.text2 }]}>我</Text>
          </View>
        )}
      </View>
    );
  }, []);

  // ── 连接中界面 ──
  if (connecting) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={theme.accent} />
          <Text style={styles.loadingText}>连接夸父 Gateway...</Text>
          <Text style={styles.loadingSubtext}>确保 Termux 中已启动夸父</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      {/* 顶栏 */}
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <TouchableOpacity onPress={handleNewChat} style={styles.newChatBtn}>
            <Text style={styles.newChatIcon}>✕</Text>
          </TouchableOpacity>
          <View style={styles.logo}>
            <Text style={styles.logoText}>夸</Text>
          </View>
          <Text style={styles.headerTitle}>夸父</Text>
        </View>
        <View style={styles.headerRight}>
          {/* 审批按钮 */}
          {approvals.length > 0 && (
            <TouchableOpacity
              style={styles.approvalBadge}
              onPress={() => setApprovalModal(true)}
            >
              <Text style={styles.approvalBadgeText}>🔐 {approvals.length}</Text>
            </TouchableOpacity>
          )}
          <TouchableOpacity
            style={styles.settingsBtn}
            onPress={() => router.push('/settings')}
          >
            <Text style={styles.settingsIcon}>⚙</Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* 消息列表 */}
      <FlatList
        ref={flatListRef}
        data={messages}
        renderItem={renderMessage}
        keyExtractor={item => item.id}
        style={styles.chatArea}
        contentContainerStyle={styles.chatContent}
        onContentSizeChange={() => flatListRef.current?.scrollToEnd({ animated: true })}
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyTitle}>夸父</Text>
            <Text style={styles.emptyText}>逐日不息 · 在指尖</Text>
          </View>
        }
      />

      {/* 工具栏按钮 + 快捷工具条 */}
      <View style={styles.toolbarArea}>
        <TouchableOpacity
          style={styles.toolbarToggle}
          onPress={() => setShowToolbar(!showToolbar)}
        >
          <Text style={[styles.toolbarToggleText, showToolbar && { color: theme.accent }]}>
            + 工具
          </Text>
        </TouchableOpacity>

        {showToolbar && (
          <View style={styles.toolbarRow}>
            <TouchableOpacity
              style={styles.toolBtn}
              onPress={() => {
                setShowToolbar(false);
                const toolMsg: StoredMessage = {
                  id: `tool-${Date.now()}`,
                  role: 'user',
                  content: '[手机工具] 正在获取位置信息...',
                  timestamp: Date.now(),
                };
                setMessages(prev => [...prev, toolMsg]);
                appendMessage(toolMsg);
                getLocation().then(loc => {
                  const reply = loc.success
                    ? `📍 当前位置：\n纬度: ${loc.data.latitude}\n经度: ${loc.data.longitude}\n地址: ${loc.data.address || '未知'}`
                    : `❌ 定位失败: ${loc.error}`;
                  const resultMsg: StoredMessage = {
                    id: `loc-${Date.now()}`,
                    role: 'assistant',
                    content: reply,
                    timestamp: Date.now(),
                  };
                  setMessages(prev => [...prev, resultMsg]);
                  appendMessage(resultMsg);
                });
              }}
            >
              <Text style={styles.toolIcon}>📍</Text>
              <Text style={styles.toolLabel}>定位</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.toolBtn}
              onPress={() => {
                setShowToolbar(false);
                getClipboard().then(clip => {
                  const reply = clip.success
                    ? `📋 剪贴板内容：\n\`${clip.data.text.slice(0, 200)}\``
                    : `❌ 读取剪贴板失败: ${clip.error}`;
                  const msg: StoredMessage = {
                    id: `clip-${Date.now()}`,
                    role: 'assistant',
                    content: reply,
                    timestamp: Date.now(),
                  };
                  setMessages(prev => [...prev, msg]);
                  appendMessage(msg);
                });
              }}
            >
              <Text style={styles.toolIcon}>📋</Text>
              <Text style={styles.toolLabel}>剪贴板</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.toolBtn}
              onPress={() => {
                setShowToolbar(false);
                sendNotification('夸父通知', '通知功能已就绪')
                  .then(() => {
                    const msg: StoredMessage = {
                      id: `notif-${Date.now()}`,
                      role: 'assistant',
                      content: '✅ 通知功能已就绪',
                      timestamp: Date.now(),
                    };
                    setMessages(prev => [...prev, msg]);
                    appendMessage(msg);
                  })
                  .catch((e: any) => {
                    const msg: StoredMessage = {
                      id: `notif-${Date.now()}`,
                      role: 'assistant',
                      content: `❌ 通知失败: ${e.message}`,
                      timestamp: Date.now(),
                    };
                    setMessages(prev => [...prev, msg]);
                    appendMessage(msg);
                  });
              }}
            >
              <Text style={styles.toolIcon}>🔔</Text>
              <Text style={styles.toolLabel}>通知</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.toolBtn}
              onPress={() => {
                setShowToolbar(false);
                // 扫码由 CameraView 组件处理，跳转到扫码页面
                router.push('/scanner');
              }}
            >
              <Text style={styles.toolIcon}>📷</Text>
              <Text style={styles.toolLabel}>扫码</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.toolBtn}
              onPress={() => {
                setShowToolbar(false);
                readDirectory('').then(files => {
                  const reply = files.success
                    ? `📁 手机文件 (${files.data.directory}):\n${files.data.files.slice(0, 30).join('\n')}`
                    : `❌ 读取失败: ${files.error}`;
                  const msg: StoredMessage = {
                    id: `file-${Date.now()}`,
                    role: 'assistant',
                    content: reply,
                    timestamp: Date.now(),
                  };
                  setMessages(prev => [...prev, msg]);
                  appendMessage(msg);
                }).catch(() => {});
              }}
            >
              <Text style={styles.toolIcon}>📁</Text>
              <Text style={styles.toolLabel}>文件</Text>
            </TouchableOpacity>
          </View>
        )}
      </View>

      {/* 输入区 */}
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={0}
      >
        <View style={styles.inputArea}>
          <View style={styles.inputRow}>
            <TextInput
              ref={inputRef}
              style={styles.input}
              value={input}
              onChangeText={setInput}
              placeholder="给夸父发消息…"
              placeholderTextColor={theme.text2}
              multiline
              maxLength={4000}
              editable={!loading}
              returnKeyType="send"
              onSubmitEditing={handleSend}
              blurOnSubmit
            />
            <TouchableOpacity
              style={[styles.sendBtn, loading && styles.sendBtnLoading]}
              onPress={handleSend}
              disabled={loading || !input.trim()}
            >
              <Text style={styles.sendBtnText}>
                {loading ? '●' : '→'}
              </Text>
            </TouchableOpacity>
          </View>
        </View>
      </KeyboardAvoidingView>

      {/* 状态栏 */}
      <View style={styles.statusBar}>
        <View style={[styles.statusDot, loading && styles.statusDotLoading]} />
        <Text style={styles.statusText}>
          {loading ? '思考中…' : status?.model ? status.model.slice(0, 20) : '就绪'}
        </Text>
      </View>

      {/* 审批模态框 */}
      <Modal
        visible={approvalModal}
        transparent
        animationType="slide"
        onRequestClose={() => setApprovalModal(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>🔐 待审批</Text>
              <TouchableOpacity onPress={() => setApprovalModal(false)}>
                <Text style={styles.modalClose}>✕</Text>
              </TouchableOpacity>
            </View>
            {approvals.length === 0 ? (
              <Text style={styles.noApprovals}>无待审批</Text>
            ) : (
              approvals.map(a => (
                <View key={a.id} style={styles.approvalCard}>
                  <View style={styles.approvalHeader}>
                    <Text style={styles.approvalTool}>{a.tool}</Text>
                    <Text style={[styles.approvalRisk, a.risk === 'high' && styles.approvalRiskHigh]}>
                      {(a.risk || 'medium').toUpperCase()}
                    </Text>
                  </View>
                  <Text style={styles.approvalDetail} numberOfLines={3}>
                    {a.detail || JSON.stringify(a)}
                  </Text>
                  <View style={styles.approvalActions}>
                    <TouchableOpacity
                      style={styles.btnApprove}
                      onPress={() => handleApprove(a.id)}
                    >
                      <Text style={styles.btnText}>批准</Text>
                    </TouchableOpacity>
                    <TouchableOpacity
                      style={styles.btnReject}
                      onPress={() => handleReject(a.id)}
                    >
                      <Text style={styles.btnText}>拒绝</Text>
                    </TouchableOpacity>
                  </View>
                </View>
              ))
            )}
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  // ── 连接中 ──
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    gap: 12,
  },
  loadingText: {
    color: theme.text,
    fontSize: 16,
    fontWeight: '600',
  },
  loadingSubtext: {
    color: theme.text2,
    fontSize: 13,
  },
  // ── 顶栏 ──
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: theme.surface,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  headerRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  newChatBtn: {
    padding: 4,
  },
  newChatIcon: {
    color: theme.text2,
    fontSize: 14,
  },
  logo: {
    width: 28,
    height: 28,
    borderRadius: 6,
    backgroundColor: theme.accent,
    justifyContent: 'center',
    alignItems: 'center',
  },
  logoText: {
    color: '#fff',
    fontWeight: '700',
    fontSize: 14,
  },
  headerTitle: {
    color: theme.text,
    fontSize: 16,
    fontWeight: '600',
  },
  settingsBtn: {
    padding: 4,
  },
  settingsIcon: {
    fontSize: 18,
  },
  approvalBadge: {
    backgroundColor: 'rgba(250,82,82,0.15)',
    borderRadius: 12,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  approvalBadgeText: {
    color: theme.error,
    fontSize: 11,
    fontWeight: '600',
  },
  // ── 消息 ──
  chatArea: {
    flex: 1,
  },
  chatContent: {
    padding: 12,
    flexGrow: 1,
  },
  msgRow: {
    flexDirection: 'row',
    gap: 8,
    paddingVertical: 8,
    borderBottomWidth: 0.5,
    borderBottomColor: theme.border,
    alignItems: 'flex-start',
  },
  msgRowUser: {
    flexDirection: 'row-reverse',
  },
  avatar: {
    width: 26,
    height: 26,
    borderRadius: 13,
    backgroundColor: theme.accent,
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: 2,
  },
  avatarUser: {
    backgroundColor: theme.surface2,
    borderWidth: 1,
    borderColor: theme.border,
  },
  avatarText: {
    color: '#fff',
    fontSize: 11,
    fontWeight: '600',
  },
  msgBubble: {
    flex: 1,
    minWidth: 0,
  },
  msgBubbleUser: {
    backgroundColor: theme.surface2,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 16,
    borderBottomRightRadius: 4,
    maxWidth: '80%',
  },
  msgText: {
    color: theme.text,
    fontSize: 14,
    lineHeight: 21,
  },
  inlineCode: {
    backgroundColor: theme.surface2,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
    fontSize: 12,
    paddingHorizontal: 4,
    borderRadius: 3,
    color: theme.accent,
  },
  link: {
    color: theme.info,
    textDecorationLine: 'underline',
  },
  // ── 空状态 ──
  emptyContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingTop: 80,
  },
  emptyTitle: {
    color: theme.text,
    fontSize: 22,
    fontWeight: '600',
    marginBottom: 4,
  },
  emptyText: {
    color: theme.text2,
    fontSize: 13,
  },
  // ── 输入区 ──
  inputArea: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    backgroundColor: theme.surface,
    borderTopWidth: 1,
    borderTopColor: theme.border,
  },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 6,
    backgroundColor: theme.surface2,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: theme.border,
    paddingLeft: 14,
    paddingRight: 2,
    paddingVertical: 2,
  },
  input: {
    flex: 1,
    color: theme.text,
    fontSize: 15,
    paddingVertical: 8,
    maxHeight: 100,
    minHeight: 22,
  },
  sendBtn: {
    width: 34,
    height: 34,
    borderRadius: 17,
    backgroundColor: theme.accent,
    justifyContent: 'center',
    alignItems: 'center',
  },
  sendBtnLoading: {
    backgroundColor: theme.error,
    opacity: 0.4,
  },
  sendBtnText: {
    color: '#fff',
    fontSize: 16,
  },
  // ── 状态栏 ──
  statusBar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 4,
    backgroundColor: theme.surface,
    borderTopWidth: 1,
    borderTopColor: theme.border,
  },
  statusDot: {
    width: 5,
    height: 5,
    borderRadius: 3,
    backgroundColor: theme.success,
  },
  statusDotLoading: {
    backgroundColor: theme.accent,
  },
  statusText: {
    color: theme.text2,
    fontSize: 11,
    flex: 1,
  },
  // ── 手机工具条 ──
  toolbarArea: {
    backgroundColor: theme.surface,
    borderTopWidth: 1,
    borderTopColor: theme.border,
    paddingHorizontal: 10,
    paddingBottom: 4,
  },
  toolbarToggle: {
    paddingVertical: 4,
  },
  toolbarToggleText: {
    color: theme.text2,
    fontSize: 12,
    fontWeight: '500',
  },
  toolbarRow: {
    flexDirection: 'row',
    gap: 4,
    paddingVertical: 6,
  },
  toolBtn: {
    flex: 1,
    alignItems: 'center',
    paddingVertical: 8,
    backgroundColor: theme.surface2,
    borderRadius: 8,
    gap: 2,
  },
  toolIcon: {
    fontSize: 18,
  },
  toolLabel: {
    color: theme.text2,
    fontSize: 10,
  },
  // ── 审批模态框 ──
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: theme.surface,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    padding: 16,
    maxHeight: '70%',
    borderTopWidth: 1,
    borderTopColor: theme.border,
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
    paddingBottom: 8,
    borderBottomWidth: 1,
    borderBottomColor: theme.border,
  },
  modalTitle: {
    color: theme.text,
    fontSize: 15,
    fontWeight: '600',
  },
  modalClose: {
    color: theme.text2,
    fontSize: 18,
    padding: 4,
  },
  noApprovals: {
    color: theme.text2,
    fontSize: 13,
    textAlign: 'center',
    paddingVertical: 20,
  },
  approvalCard: {
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 8,
    padding: 12,
    marginBottom: 8,
    backgroundColor: theme.surface2,
  },
  approvalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 4,
  },
  approvalTool: {
    color: theme.text,
    fontSize: 12,
    fontWeight: '500',
  },
  approvalRisk: {
    fontSize: 10,
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: 3,
    backgroundColor: 'rgba(252,196,25,0.15)',
    color: '#fcc419',
    overflow: 'hidden',
  },
  approvalRiskHigh: {
    backgroundColor: 'rgba(250,82,82,0.15)',
    color: theme.error,
  },
  approvalDetail: {
    color: theme.text2,
    fontSize: 11,
    marginBottom: 8,
    lineHeight: 16,
  },
  approvalActions: {
    flexDirection: 'row',
    gap: 8,
  },
  btnApprove: {
    flex: 1,
    backgroundColor: theme.success,
    borderRadius: 6,
    paddingVertical: 8,
    alignItems: 'center',
  },
  btnReject: {
    flex: 1,
    backgroundColor: theme.error,
    borderRadius: 6,
    paddingVertical: 8,
    alignItems: 'center',
  },
  btnText: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '600',
  },
});
