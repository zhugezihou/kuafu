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
  AppState,
  Animated,
  ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import { theme } from '../src/theme';
import {
  sendMessage,
  getStatus,
  connectSSE,
  startSSEPolling,
  SSEEvent,
  StatusResponse,
} from '../src/api/gateway';

// ── 消息类型 ──
interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  isStreaming?: boolean;
}

// ── 简易 Markdown 渲染 ──
function renderMarkdown(text: string): { plain: string; segments: { text: string; bold?: boolean; code?: boolean; link?: string }[] } {
  // 简易版：提取纯文本（在 RN 中用 Text 组件渲染带样式的片段）
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
  const { segments } = renderMarkdown(content);
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
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [connecting, setConnecting] = useState(true);
  const flatListRef = useRef<FlatList>(null);
  const inputRef = useRef<TextInput>(null);
  const fadeAnim = useRef(new Animated.Value(0)).current;

  // ── 初始化 ──
  useEffect(() => {
    initApp();
  }, []);

  async function initApp() {
    setConnecting(true);
    // 尝试连接 Gateway
    for (let i = 0; i < 5; i++) {
      try {
        const st = await getStatus();
        setStatus(st);
        setConnecting(false);
        // 添加欢迎消息
        setMessages([{
          id: 'welcome',
          role: 'assistant',
          content: `你好！我是夸父 v${st.version || '?'}，有什么可以帮你的？`,
          timestamp: Date.now(),
        }]);
        Animated.timing(fadeAnim, {
          toValue: 1,
          duration: 300,
          useNativeDriver: true,
        }).start();
        return;
      } catch {
        await new Promise(r => setTimeout(r, 2000));
      }
    }
    setConnecting(false);
    // 离线模式
    setMessages([{
      id: 'offline',
      role: 'assistant',
      content: '⚠️ 无法连接到夸父 Gateway。请确保已在 Termux 中启动夸父。',
      timestamp: Date.now(),
    }]);
  }

  // ── 发送消息 ──
  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || loading) return;

    setInput('');
    const userMsg: Message = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: text,
      timestamp: Date.now(),
    };

    const assistantMsg: Message = {
      id: `assistant-${Date.now()}`,
      role: 'assistant',
      content: '',
      isStreaming: true,
      timestamp: Date.now(),
    };

    setMessages(prev => [...prev, userMsg, assistantMsg]);
    setLoading(true);

    try {
      const result = await sendMessage(text);
      const reply = result.message || result.summary || (result.success ? '完成' : '出错');

      setMessages(prev =>
        prev.map(m =>
          m.id === assistantMsg.id
            ? { ...m, content: reply, isStreaming: false }
            : m
        )
      );
    } catch (e: any) {
      setMessages(prev =>
        prev.map(m =>
          m.id === assistantMsg.id
            ? { ...m, content: `❌ 错误: ${e.message}`, isStreaming: false }
            : m
        )
      );
    }

    setLoading(false);
    // 滚动到最新消息
    setTimeout(() => flatListRef.current?.scrollToEnd({ animated: true }), 100);
  }, [input, loading]);

  // ── 渲染消息 ──
  const renderMessage = useCallback(({ item }: { item: Message }) => {
    const isUser = item.role === 'user';
    return (
      <View style={[styles.msgRow, isUser && styles.msgRowUser]}>
        {!isUser && (
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>夸</Text>
          </View>
        )}
        <View style={[styles.msgBubble, isUser ? styles.msgBubbleUser : styles.msgBubbleAssistant]}>
          {item.isStreaming && !item.content ? (
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
          <View style={styles.logo}>
            <Text style={styles.logoText}>夸</Text>
          </View>
          <Text style={styles.headerTitle}>夸父</Text>
        </View>
        <TouchableOpacity
          style={styles.settingsBtn}
          onPress={() => router.push('/settings')}
        >
          <Text style={styles.settingsIcon}>⚙</Text>
        </TouchableOpacity>
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
  msgBubbleAssistant: {
    // 无背景色，与消息列表统一
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
});
