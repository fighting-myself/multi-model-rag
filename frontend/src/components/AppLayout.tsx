import { Layout, Menu, Avatar, Dropdown, Button, Space } from 'antd'
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
} from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import { useThemeStore } from '../stores/themeStore'

const { Header, Sider, Content } = Layout

interface AppLayoutProps {
  children: React.ReactNode
}

const logoStyle: React.CSSProperties = {
  height: 64,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  borderBottom: '1px solid rgba(0, 245, 255, 0.12)',
}

export default function AppLayout({ children }: AppLayoutProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuthStore()
  const { theme, toggleTheme } = useThemeStore()

  const menuItems = [
    { key: '/', icon: <HomeOutlined />, label: '首页' },
    { key: '/files', icon: <FileOutlined />, label: '文件管理' },
    { key: '/knowledge-bases', icon: <DatabaseOutlined />, label: '知识库' },
    { key: '/image-search', icon: <PictureOutlined />, label: '多模态检索' },
    { key: '/chat', icon: <MessageOutlined />, label: '智能问答' },
    { key: '/billing', icon: <DollarOutlined />, label: '计费中心' },
    { key: '/audit-log', icon: <AuditOutlined />, label: '审计日志' },
    { key: '/mcp-servers', icon: <ApiOutlined />, label: 'MCP 工具' },
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
    <Layout style={{ minHeight: '100vh', background: 'transparent', position: 'relative' }}>
      <div className="app-bg-canvas" aria-hidden />
      <Sider width={220} className="tech-sider" style={{ position: 'relative', zIndex: 2 }}>
        <div style={logoStyle} className="tech-logo-wrap">
          <span className="tech-logo-text">
            RAG 助手
          </span>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={handleMenuClick}
          style={{
            marginTop: 16,
            background: 'transparent',
            border: 'none',
            color: 'rgba(255,255,255,0.75)',
          }}
          theme="dark"
        />
      </Sider>
      <Layout style={{ position: 'relative', zIndex: 1 }}>
        <Header
          className="tech-header"
          style={{
            padding: '0 24px',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            height: 64,
          }}
        >
          <div />
          <Space size="middle">
            <Button
              type="text"
              icon={theme === 'dark' ? <BulbFilled /> : <BulbOutlined />}
              onClick={toggleTheme}
              style={{ color: 'rgba(255,255,255,0.9)' }}
              title={theme === 'dark' ? '切换为亮色' : '切换为暗色'}
            />
            <Dropdown
              menu={{ items: userMenuItems, onClick: handleMenuClick }}
              placement="bottomRight"
              overlayStyle={{ minWidth: 140 }}
            >
              <Button
                type="text"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  color: 'rgba(255,255,255,0.9)',
                  height: 40,
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
                <span style={{ fontWeight: 500 }}>{user?.username || '用户'}</span>
              </Button>
            </Dropdown>
          </Space>
        </Header>
        <Content className="app-content-area app-content-area-padding">
          {children}
        </Content>
      </Layout>
    </Layout>
  )
}
