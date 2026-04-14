import { useState } from 'react'
import { Layout, Menu, Avatar, Dropdown, Button } from 'antd'
import {
  HomeOutlined,
  FileOutlined,
  DatabaseOutlined,
  MessageOutlined,
  DollarOutlined,
  PictureOutlined,
  UserOutlined,
  LogoutOutlined,
  AuditOutlined,
  ApiOutlined,
  BulbOutlined,
  BulbFilled,
  RobotOutlined,
  DesktopOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  LineChartOutlined,
} from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import { useThemeStore } from '../stores/themeStore'

const { Header, Sider, Content } = Layout

interface AppLayoutProps {
  children: React.ReactNode
}

const logoStyle: React.CSSProperties = {
  height: 48,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  borderBottom: '1px solid rgba(0, 245, 255, 0.12)',
}

export default function AppLayout({ children }: AppLayoutProps) {
  const [siderCollapsed, setSiderCollapsed] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuthStore()
  const { theme, toggleTheme } = useThemeStore()

  const selectedMenuKey = location.pathname.startsWith('/multi-agent')
    ? '/multi-agent/plan_execute'
    : location.pathname

  const menuItems = [
    { key: '/', icon: <HomeOutlined />, label: '首页' },
    { key: '/files', icon: <FileOutlined />, label: '文件管理' },
    { key: '/knowledge-bases', icon: <DatabaseOutlined />, label: '知识库' },
    { key: '/chat', icon: <MessageOutlined />, label: '智能问答' },
    { key: '/image-search', icon: <PictureOutlined />, label: '多模态检索' },
    { key: '/recall-evaluation', icon: <LineChartOutlined />, label: '召回率评测' },
    { key: '/advanced-rag-metrics', icon: <BulbOutlined />, label: 'RAG 指标' },
    { key: '/external-connections', icon: <ApiOutlined />, label: '外接平台连接' },
    { key: '/mcp-servers', icon: <ApiOutlined />, label: 'MCP 工具' },
    { key: '/steward', icon: <RobotOutlined />, label: '浏览器助手' },
    { key: '/computer-steward', icon: <DesktopOutlined />, label: '电脑管家' },
    { key: '/audit-log', icon: <AuditOutlined />, label: '审计日志' },
    { key: '/billing', icon: <DollarOutlined />, label: '计费中心' },
    { key: '/multi-agent/plan_execute', icon: <RobotOutlined />, label: '单智能体' },
  ]

  const userMenuItems = [
    { key: 'profile', icon: <UserOutlined />, label: '个人中心' },
    { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
  ]

  const handleMenuClick = ({ key }: { key: string }) => {
    if (key === 'logout') {
      logout()
      navigate('/login')
    } else if (key === 'profile') {
      navigate('/profile')
    } else {
      navigate(key)
    }
  }

  return (
    <Layout style={{ minHeight: '100vh', height: '100vh', overflow: 'hidden', background: 'transparent', position: 'relative', display: 'flex' }}>
      <div className="app-bg-canvas" aria-hidden />
      <Sider
        width={220}
        collapsedWidth={64}
        collapsed={siderCollapsed}
        onCollapse={setSiderCollapsed}
        collapsible
        trigger={null}
        className="tech-sider"
        style={{ position: 'relative', zIndex: 2, display: 'flex', flexDirection: 'column' }}
      >
        <div
          style={{
            ...logoStyle,
            display: 'flex',
            alignItems: 'center',
            justifyContent: siderCollapsed ? 'center' : 'space-between',
            paddingLeft: siderCollapsed ? 0 : 16,
            paddingRight: siderCollapsed ? 0 : 8,
          }}
          className="tech-logo-wrap"
        >
          <span className="tech-logo-text" style={{ opacity: siderCollapsed ? 0 : 1, overflow: 'hidden', whiteSpace: 'nowrap', transition: 'opacity 0.2s', flexShrink: 0 }}>
            RAG 助手
          </span>
          <Button
            type="text"
            size="small"
            icon={siderCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setSiderCollapsed(!siderCollapsed)}
            style={{
              flexShrink: 0,
              color: 'var(--app-text-secondary)',
              width: 32,
              height: 32,
              padding: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
            title={siderCollapsed ? '展开侧边栏' : '收起侧边栏'}
          />
        </div>
        <div style={{ flex: 1, overflow: 'auto', paddingBottom: 56 }}>
          <Menu
            mode="inline"
            selectedKeys={[selectedMenuKey]}
            items={menuItems}
            onClick={handleMenuClick}
            style={{
              marginTop: 0,
              background: 'transparent',
              border: 'none',
            }}
            theme={theme === 'dark' ? 'dark' : 'light'}
            inlineCollapsed={siderCollapsed}
            className="tech-sider-menu"
          />
        </div>
      </Sider>
      {/* 固定在视口左下角，不随侧栏菜单滚动 */}
      <div
        className="tech-sider-footer tech-sider-footer-fixed"
        style={{
          position: 'fixed',
          left: 0,
          bottom: 0,
          width: siderCollapsed ? 64 : 220,
          borderTop: '1px solid var(--app-glass-border)',
          padding: siderCollapsed ? '12px 8px' : '12px 16px',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          flexDirection: siderCollapsed ? 'column' : 'row',
          zIndex: 3,
          transition: 'width 0.2s',
        }}
      >
        <Button
          type="text"
          size="small"
          icon={theme === 'dark' ? <BulbFilled /> : <BulbOutlined />}
          onClick={toggleTheme}
          style={{ color: 'var(--app-text-primary)' }}
          title={theme === 'dark' ? '切换为亮色' : '切换为暗色'}
        />
          <Dropdown
            menu={{ items: userMenuItems, onClick: handleMenuClick }}
            placement="topLeft"
            overlayStyle={{ minWidth: 140 }}
          >
          <Button
            type="text"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: siderCollapsed ? 0 : 8,
              color: 'var(--app-text-primary)',
              padding: siderCollapsed ? '4px' : '4px 8px',
              width: siderCollapsed ? '100%' : 'auto',
              justifyContent: siderCollapsed ? 'center' : 'flex-start',
            }}
          >
            <Avatar
              size="small"
              className="tech-avatar"
              style={{
                background: 'linear-gradient(135deg, #00f5ff, #a855f7)',
                boxShadow: '0 0 12px rgba(0, 245, 255, 0.5)',
              }}
              icon={<UserOutlined />}
            />
            {!siderCollapsed && <span style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis' }}>{user?.username || '用户'}</span>}
          </Button>
        </Dropdown>
      </div>
      <Layout
        style={{
          position: 'relative',
          zIndex: 1,
          flex: 1,
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
          ['--app-sider-width' as string]: siderCollapsed ? '64px' : '220px',
        }}
      >
        <Header
          className="tech-header"
          style={{
            padding: '0 24px',
            minHeight: 48,
            height: 48,
          }}
        />
        <Content className="app-content-area app-content-area-padding">
          {children}
        </Content>
      </Layout>
    </Layout>
  )
}
