import { useState, useEffect } from 'react'
import { Form, Input, Button, Card, message } from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import Lottie from 'lottie-react'
import api from '../services/api'
import { useAuthStore } from '../stores/authStore'

// Lottie 动画 JSON（与 airbnb/lottie 同源格式，Web 端用 lottie-react 渲染）
const LOTTIE_ANIMATION_URL = 'https://assets10.lottiefiles.com/packages/lf20_ktwnwv5m.json'

export default function Login() {
  const navigate = useNavigate()
  const { setToken, setUser } = useAuthStore()
  const [loading, setLoading] = useState(false)
  const [animationData, setAnimationData] = useState<object | null>(null)

  useEffect(() => {
    fetch(LOTTIE_ANIMATION_URL)
      .then((res) => res.json())
      .then(setAnimationData)
      .catch(() => setAnimationData(null))
  }, [])

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true)
    try {
      const formData = new FormData()
      formData.append('username', values.username)
      formData.append('password', values.password)

      const response = await api.post('/auth/login', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      })

      setToken(response.access_token)
      const userResponse = await api.get('/auth/me')
      setUser(userResponse)
      message.success('登录成功')
      navigate('/')
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string | string[] } }; message?: string }
      const detail = e.response?.data?.detail
      const msg =
        typeof detail === 'string'
          ? detail
          : Array.isArray(detail)
            ? detail[0] ?? '登录失败'
            : e.response?.data?.message ?? e.message ?? '登录失败'
      message.error(msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="app-fullpage-tech">
      <div className="login-page-wrap">
        <Card className="login-card">
          <div className="login-card-inner">
            {animationData && (
              <div className="login-lottie-wrap">
                <Lottie
                  animationData={animationData}
                  loop
                  style={{ width: 140, height: 140 }}
                />
              </div>
            )}
            <h1 className="login-title">AI多模态智能问答助手</h1>
            <p className="login-subtitle">登录以继续使用</p>
            <Form
              name="login"
              onFinish={onFinish}
              autoComplete="off"
              layout="vertical"
              requiredMark={false}
            >
              <Form.Item
                name="username"
                rules={[{ required: true, message: '请输入用户名' }]}
              >
                <Input
                  prefix={<UserOutlined className="login-input-icon" />}
                  placeholder="用户名"
                  size="large"
                  className="login-input"
                />
              </Form.Item>

              <Form.Item
                name="password"
                rules={[{ required: true, message: '请输入密码' }]}
              >
                <Input.Password
                  prefix={<LockOutlined className="login-input-icon" />}
                  placeholder="密码"
                  size="large"
                  className="login-input"
                />
              </Form.Item>

              <Form.Item className="login-submit-item">
                <Button
                  type="primary"
                  htmlType="submit"
                  block
                  size="large"
                  loading={loading}
                  className="login-btn"
                >
                  登录
                </Button>
              </Form.Item>

              <div className="login-footer">
                <a href="/register">还没有账号？立即注册</a>
              </div>
            </Form>
          </div>
        </Card>
      </div>
    </div>
  )
}
