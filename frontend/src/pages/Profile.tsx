import { useState, useEffect } from 'react'
import { Card, Descriptions, Form, Input, Button, message } from 'antd'
import { LockOutlined } from '@ant-design/icons'
import api from '../services/api'
import { useAuthStore } from '../stores/authStore'

interface UserInfo {
  id: number
  username: string
  email: string
  phone?: string
  avatar_url?: string
  role: string
  plan_id?: number
  credits: number
  is_active: boolean
  created_at: string
}

export default function Profile() {
  const { user: storeUser, setUser } = useAuthStore()
  const [user, setUserInfo] = useState<UserInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [passwordLoading, setPasswordLoading] = useState(false)
  const [form] = Form.useForm()

  useEffect(() => {
    api
      .get<UserInfo>('/auth/me')
      .then((data) => {
        setUserInfo(data)
        setUser(data)
      })
      .catch(() => message.error('获取用户信息失败'))
      .finally(() => setLoading(false))
  }, [setUser])

  const displayUser = user ?? (storeUser as UserInfo | null)

  const onPasswordFinish = async (values: { old_password: string; new_password: string }) => {
    setPasswordLoading(true)
    try {
      await api.put('/auth/me/password', {
        old_password: values.old_password,
        new_password: values.new_password,
      })
      message.success('密码已修改，请妥善保管')
      form.resetFields()
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      message.error(err.response?.data?.detail || '修改密码失败')
    } finally {
      setPasswordLoading(false)
    }
  }

  return (
    <div>
      <h1 className="app-page-title">个人中心</h1>
      <p className="app-page-desc">账户信息与密码修改</p>

      <Card title="注册信息" loading={loading} style={{ marginBottom: 24 }}>
        {displayUser && (
          <Descriptions column={1} bordered>
            <Descriptions.Item label="用户名">{displayUser.username}</Descriptions.Item>
            <Descriptions.Item label="邮箱">{displayUser.email}</Descriptions.Item>
            <Descriptions.Item label="手机">{displayUser.phone || '-'}</Descriptions.Item>
            <Descriptions.Item label="角色">{displayUser.role}</Descriptions.Item>
            <Descriptions.Item label="积分">{displayUser.credits}</Descriptions.Item>
            <Descriptions.Item label="注册时间">
              {displayUser.created_at
                ? new Date(displayUser.created_at).toLocaleString('zh-CN')
                : '-'}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Card>

      <Card title="修改密码">
        <Form
          form={form}
          name="password"
          onFinish={onPasswordFinish}
          layout="vertical"
          style={{ maxWidth: 400 }}
        >
          <Form.Item
            name="old_password"
            label="当前密码"
            rules={[{ required: true, message: '请输入当前密码' }]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="当前密码" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 6, message: '密码至少 6 位' },
            ]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="新密码" />
          </Form.Item>
          <Form.Item
            name="confirm"
            label="确认新密码"
            dependencies={['new_password']}
            rules={[
              { required: true, message: '请确认新密码' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('new_password') === value) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('两次输入的新密码不一致'))
                },
              }),
            ]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="确认新密码" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={passwordLoading}>
              修改密码
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )
}
