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
  ApiOutlined
} from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'

const { Header, Sider, Content } = Layout

interface AppLayoutProps {
  children: React.ReactNode
}

const siderStyle: React.CSSProperties = {
  background: 'linear-gradient(180deg, #0f172a 0%, #1e293b 100%)',
  boxShadow: '4px 0 24px rgba(0,0,0,0.08)',
}

const logoStyle: React.CSSProperties = {
  height: 64,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  borderBottom: '1px solid rgba(255,255,255,0.06)',
}

const headerStyle: React.CSSProperties = {
  background: 'linear-gradient(90deg, #1e293b 0%, #334155 100%)',
  padding: '0 24px',
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  borderBottom: '1px solid rgba(255,255,255,0.06)',
  boxShadow: '0 1px 0 0 rgba(255,255,255,0.04)',
}

export default function AppLayout({ children }: AppLayoutProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuthStore()

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
    {
      key: 'profile',
      icon: <UserOutlined />,
      label: '个人中心',
    },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      danger: true,
    },
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
    <Layout style={{ minHeight: '100vh', background: 'var(--app-bg)' }}>
      <Sider width={220} style={siderStyle}>
        <div style={logoStyle}>
          <span style={{ 
            fontSize: 18, 
            fontWeight: 700, 
            color: '#fff', 
            letterSpacing: '-0.02em',
            background: 'linear-gradient(135deg, #fff 0%, #94a3b8 100%)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            backgroundClip: 'text',
          }}>
            RAG 助手
          </span>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={handleMenuClick}
          style={{
            marginTop: 12,
            background: 'transparent',
            border: 'none',
            color: 'rgba(255,255,255,0.75)',
          }}
          theme="dark"
        />
      </Sider>
      <Layout>
        <Header style={headerStyle}>
          <div />
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
              <Avatar size="small" style={{ background: 'var(--app-accent)' }} icon={<UserOutlined />} />
              <span style={{ fontWeight: 500 }}>{user?.username || '用户'}</span>
            </Button>
          </Dropdown>
        </Header>
        <Content className="app-content-area" style={{ margin: 24, padding: 24, borderRadius: 12 }}>
          {children}
        </Content>
      </Layout>
    </Layout>
  )
}
