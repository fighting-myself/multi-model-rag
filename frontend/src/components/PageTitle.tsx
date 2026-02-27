import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

const TITLE_MAP: Record<string, string> = {
  '/': '仪表盘',
  '/login': '登录',
  '/register': '注册',
  '/files': '文件管理',
  '/knowledge-bases': '知识库',
  '/chat': '智能问答',
  '/billing': '计费中心',
  '/profile': '个人中心',
  '/image-search': '多模态检索',
  '/audit-log': '操作审计',
  '/mcp-servers': 'MCP 工具',
}

const APP_NAME = 'RAG 助手'

export default function PageTitle() {
  const { pathname } = useLocation()
  useEffect(() => {
    const title = TITLE_MAP[pathname] ? `${TITLE_MAP[pathname]} - ${APP_NAME}` : APP_NAME
    document.title = title
  }, [pathname])
  return null
}
