import { Skeleton } from 'antd'

interface PageSkeletonProps {
  rows?: number
  title?: boolean
}

export default function PageSkeleton({ rows = 4, title = true }: PageSkeletonProps) {
  return (
    <div style={{ padding: 24 }}>
      {title && <Skeleton.Input active style={{ width: 200, marginBottom: 24 }} />}
      <Skeleton active paragraph={{ rows }} />
    </div>
  )
}
