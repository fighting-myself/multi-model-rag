import { Layout, Menu, Avatar, Dropdown, Button } from 'antd'
import { 
  HomeOutlined, 
  FileOutlined, 
  DatabaseOutlined, 
  MessageOutlined,
  DollarOutlined,
  UserOutlined,
  LogoutOutlined
} from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'

const { Header, Sider, Content } = Layout

interface AppLayoutProps {
  children: React.ReactNode
}

export default function AppLayout({ children }: AppLayoutProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuthStore()

  const menuItems = [
    { key: '/', icon: <HomeOutlined />, label: '首页' },
    { key: '/files', icon: <FileOutlined />, label: '文件管理' },
    { key: '/knowledge-bases', icon: <DatabaseOutlined />, label: '知识库' },
    { key: '/chat', icon: <MessageOutlined />, label: '智能问答' },
    { key: '/billing', icon: <DollarOutlined />, label: '计费中心' },
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
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={200} theme="light">
        <div style={{ 
          height: 64, 
          display: 'flex', 
          alignItems: 'center', 
          justifyContent: 'center',
          borderBottom: '1px solid #f0f0f0'
        }}>
          <h2 style={{ margin: 0 }}>RAG助手</h2>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={handleMenuClick}
        />
      </Sider>
      <Layout>
        <Header style={{ 
          background: '#fff', 
          padding: '0 24px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          borderBottom: '1px solid #f0f0f0'
        }}>
          <div></div>
          <Dropdown
            menu={{ items: userMenuItems, onClick: handleMenuClick }}
            placement="bottomRight"
          >
            <Button type="text" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Avatar icon={<UserOutlined />} />
              <span>{user?.username || '用户'}</span>
            </Button>
          </Dropdown>
        </Header>
        <Content style={{ margin: '24px', background: '#fff', padding: 24 }}>
          {children}
        </Content>
      </Layout>
    </Layout>
  )
}
