import { useState, useEffect } from 'react'
import {
  Alert,
  Card,
  Form,
  Select,
  Switch,
  Button,
  Input,
  message,
  Table,
  Space,
  Typography,
  Divider,
  Modal,
  Upload,
  Row,
  Col,
  Statistic,
  Tooltip,
  Popconfirm,
  List,
} from 'antd'
import {
  PlayCircleOutlined,
  SaveOutlined,
  UploadOutlined,
  QuestionCircleOutlined,
  DeleteOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons'
import api from '../services/api'
import PageSkeleton from '../components/PageSkeleton'
import type {
  KnowledgeBaseListResponse,
  KnowledgeBaseItem,
  RecallRunRequest,
  RecallRunResponse,
  BenchmarkItem,
  BenchmarkDatasetItem,
  BenchmarkDatasetListResponse,
} from '../types/api'

const BENCHMARK_JSON_EXAMPLE = `[
  { "query": "示例问题1", "relevant_chunk_ids": [1, 2, 3] },
  { "query": "示例问题2", "relevant_chunk_ids": [4, 5] }
]`

export default function RecallEvaluation() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseItem[]>([])
  const [initLoading, setInitLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<RecallRunResponse | null>(null)
  const [benchmarkItems, setBenchmarkItems] = useState<BenchmarkItem[]>([])
  const [benchmarkJson, setBenchmarkJson] = useState('')
  const [savedDatasets, setSavedDatasets] = useState<BenchmarkDatasetItem[]>([])
  const [savedDatasetsLoading, setSavedDatasetsLoading] = useState(false)
  const [selectedDatasetId, setSelectedDatasetId] = useState<number | null>(null)
  const [saveModalVisible, setSaveModalVisible] = useState(false)
  const [saveLoading, setSaveLoading] = useState(false)
  const [manageDatasetsVisible, setManageDatasetsVisible] = useState(false)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [form] = Form.useForm()
  const [saveForm] = Form.useForm()

  const METRIC_TIPS = {
    recall_at_1: 'Recall@1：在检索返回的前 1 个结果中，能命中标准答案的占比。对每条样本计算「命中数/标准答案数」再求平均。',
    recall_at_5: 'Recall@5：在检索返回的前 5 个结果中，能命中标准答案的占比。数值越高说明前 5 条里相关文档越多。',
    recall_at_10: 'Recall@10：在检索返回的前 10 个结果中，能命中标准答案的占比。',
    recall_at_20: 'Recall@20：在检索返回的前 20 个结果中，能命中标准答案的占比。',
    mrr: 'MRR（平均倒数排名）：对每条查询，取「第一个被命中的标准答案」在检索结果中的排名倒数（第 1 位=1，第 3 位=1/3），再对所有查询求平均。越高表示相关结果越靠前。',
    num_queries: '参与评测的查询条数（benchmark 样本数）。',
  }

  const fetchKnowledgeBases = async () => {
    setInitLoading(true)
    try {
      const res = await api.get<KnowledgeBaseListResponse>('/knowledge-bases')
      setKnowledgeBases(res.knowledge_bases || [])
    } catch {
      message.error('获取知识库列表失败')
    } finally {
      setInitLoading(false)
    }
  }

  const fetchSavedDatasets = async () => {
    setSavedDatasetsLoading(true)
    try {
      const res = await api.get<BenchmarkDatasetListResponse>('/evaluation/benchmarks')
      setSavedDatasets(res.datasets || [])
    } catch {
      message.error('获取评测数据集列表失败')
    } finally {
      setSavedDatasetsLoading(false)
    }
  }

  useEffect(() => {
    fetchKnowledgeBases()
    fetchSavedDatasets()
  }, [])

  const loadDatasetById = async (id: number) => {
    try {
      const res = await api.get<BenchmarkDatasetItem>(`/evaluation/benchmarks/${id}`)
      setBenchmarkItems(res.items || [])
      setBenchmarkJson(JSON.stringify(res.items || [], null, 2))
      setSelectedDatasetId(id)
    } catch {
      message.error('加载评测数据集失败')
    }
  }

  const parseBenchmarkFromJson = (): BenchmarkItem[] | null => {
    const raw = benchmarkJson.trim()
    if (!raw) return null
    try {
      const data = JSON.parse(raw)
      const list = Array.isArray(data) ? data : (data?.items ? data.items : [])
      const items: BenchmarkItem[] = list.map((x: { query?: string; relevant_chunk_ids?: number[] }) => ({
        query: String(x?.query ?? '').trim(),
        relevant_chunk_ids: Array.isArray(x?.relevant_chunk_ids) ? x.relevant_chunk_ids : [],
      })).filter((x: BenchmarkItem) => x.query)
      return items
    } catch (e) {
      message.error('Benchmark JSON 格式错误，请检查')
      return null
    }
  }

  const handleRun = async () => {
    const values = await form.validateFields().catch(() => null)
    if (!values) return
    const kbId = values.knowledge_base_id
    if (!kbId) {
      message.warning('请选择知识库')
      return
    }
    const items = selectedDatasetId ? benchmarkItems : parseBenchmarkFromJson()
    if (!items || items.length === 0) {
      message.warning('请填写或选择评测数据（至少一条：query + relevant_chunk_ids）')
      return
    }
    setRunning(true)
    setResult(null)
    message.loading({ content: '评测中…样本较多或开启 Rerank/查询改写时可能需 1～3 分钟', key: 'recall-run', duration: 0 })
    try {
      const payload: RecallRunRequest = {
        knowledge_base_id: kbId,
        retrieval_config: {
          retrieval_mode: values.retrieval_mode || 'hybrid',
          use_rerank: values.use_rerank !== false,
          use_query_expand: values.use_query_expand === true,
        },
        benchmark: { items },
        top_k_list: [1, 5, 10, 20],
      }
      const res = await api.post<RecallRunResponse>('/evaluation/recall/run', payload, { timeout: 300000 })
      setResult(res)
      message.success({ content: '评测完成', key: 'recall-run' })
    } catch (e: unknown) {
      message.destroy('recall-run')
      const msg = e && typeof e === 'object' && 'message' in e ? String((e as { message: string }).message) : '评测失败'
      message.error(msg)
    } finally {
      message.destroy('recall-run')
      setRunning(false)
    }
  }

  const handleDeleteDataset = async (id: number) => {
    setDeletingId(id)
    try {
      await api.delete(`/evaluation/benchmarks/${id}`)
      message.success('已删除')
      if (selectedDatasetId === id) {
        setSelectedDatasetId(null)
        setBenchmarkItems([])
        setBenchmarkJson('')
      }
      fetchSavedDatasets()
    } catch {
      message.error('删除失败')
    } finally {
      setDeletingId(null)
    }
  }

  const handleSaveBenchmark = async () => {
    const values = await saveForm.validateFields().catch(() => null)
    if (!values) return
    const items = selectedDatasetId ? benchmarkItems : parseBenchmarkFromJson()
    if (!items || items.length === 0) {
      message.warning('请先填写或加载评测数据再保存')
      return
    }
    setSaveLoading(true)
    try {
      await api.post('/evaluation/benchmarks', {
        name: values.name,
        description: values.description || undefined,
        knowledge_base_id: values.knowledge_base_id || undefined,
        items,
      })
      message.success('保存成功')
      setSaveModalVisible(false)
      saveForm.resetFields()
      fetchSavedDatasets()
    } catch {
      message.error('保存失败')
    } finally {
      setSaveLoading(false)
    }
  }

  const handleUploadFile = (file: File) => {
    const reader = new FileReader()
    reader.onload = () => {
      const text = String(reader.result ?? '')
      setBenchmarkJson(text)
      setSelectedDatasetId(null)
      const parsed = (() => {
        try {
          const data = JSON.parse(text)
          return Array.isArray(data) ? data : (data?.items ? data.items : [])
        } catch {
          return []
        }
      })()
      setBenchmarkItems(parsed.map((x: { query?: string; relevant_chunk_ids?: number[] }) => ({
        query: String(x?.query ?? '').trim(),
        relevant_chunk_ids: Array.isArray(x?.relevant_chunk_ids) ? x.relevant_chunk_ids : [],
      })))
      message.info(`已加载 ${parsed.length} 条样本`)
    }
    reader.readAsText(file, 'UTF-8')
    return false
  }

  const pct = (v: number | undefined) => (v != null ? (v * 100).toFixed(1) + '%' : '-')
  const isAllZero =
    result &&
    result.metrics &&
    (result.metrics.recall_at_1 ?? 0) === 0 &&
    (result.metrics.recall_at_5 ?? 0) === 0 &&
    (result.metrics.recall_at_10 ?? 0) === 0 &&
    (result.metrics.mrr ?? 0) === 0
  const columns = [
    {
      title: '查询',
      dataIndex: 'query',
      key: 'query',
      ellipsis: true,
      render: (t: string) => <Typography.Text ellipsis={{ tooltip: t }}>{t || '-'}</Typography.Text>,
    },
    {
      title: '标准答案 ID',
      key: 'relevant_ids',
      width: 120,
      render: (_: unknown, row: { relevant_ids?: number[] }) =>
        (row.relevant_ids?.length ? row.relevant_ids.join(', ') : '-'),
    },
    {
      title: '检索到的 ID(前5)',
      key: 'retrieved_ids',
      width: 140,
      render: (_: unknown, row: { retrieved_ids?: number[] }) =>
        (row.retrieved_ids?.length ? row.retrieved_ids.slice(0, 5).join(', ') : '（无）'),
    },
    { title: 'Recall@1', key: 'r1', width: 90, render: (_: unknown, row: { recall_at_k?: Record<number, number> }) => pct(row.recall_at_k?.[1]) },
    { title: 'Recall@5', key: 'r5', width: 90, render: (_: unknown, row: { recall_at_k?: Record<number, number> }) => pct(row.recall_at_k?.[5]) },
    { title: 'Recall@10', key: 'r10', width: 95, render: (_: unknown, row: { recall_at_k?: Record<number, number> }) => pct(row.recall_at_k?.[10]) },
    { title: 'Recall@20', key: 'r20', width: 95, render: (_: unknown, row: { recall_at_k?: Record<number, number> }) => pct(row.recall_at_k?.[20]) },
    { title: 'MRR', dataIndex: 'mrr', key: 'mrr', width: 80, render: (v: number) => (v != null ? v.toFixed(3) : '-') },
  ]

  if (initLoading) {
    return <PageSkeleton />
  }

  return (
    <div className="app-page app-perspective">
      <div className="app-page-header">
        <h1 className="app-page-title app-animate-in">召回率评测</h1>
        <p className="app-page-desc app-animate-in app-animate-in-delay-1">
          选择知识库与检索方式组合，使用 Benchmark 数据评测 Recall@k、Hit@k、MRR，对比不同策略效果
        </p>
      </div>

      <Card className="app-card-3d app-animate-in" style={{ marginBottom: 24 }}>
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            knowledge_base_id: undefined,
            retrieval_mode: 'hybrid',
            use_rerank: true,
            use_query_expand: false,
          }}
        >
          <Row gutter={24}>
            <Col xs={24} md={12}>
              <Form.Item
                name="knowledge_base_id"
                label="知识库"
                rules={[{ required: true, message: '请选择知识库' }]}
              >
                <Select
                  placeholder="选择要评测的知识库"
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  options={knowledgeBases.map((kb) => ({ label: `${kb.name}（${kb.chunk_count} 块）`, value: kb.id }))}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item
                name="retrieval_mode"
                label={
                  <Space>
                    检索方式
                    <Tooltip title="vector=仅向量检索；fulltext=仅全文(BM25)；hybrid=向量+全文 RRF 融合">
                      <QuestionCircleOutlined />
                    </Tooltip>
                  </Space>
                }
              >
                <Select
                  options={[
                    { label: '仅向量', value: 'vector' },
                    { label: '仅全文 (BM25)', value: 'fulltext' },
                    { label: '混合 (向量+全文 RRF)', value: 'hybrid' },
                  ]}
                />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={24}>
            <Col xs={24} sm={12} md={6}>
              <Form.Item name="use_rerank" label="使用 Rerank" valuePropName="checked">
                <Switch checkedChildren="开" unCheckedChildren="关" />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Form.Item name="use_query_expand" label="查询改写/子问题扩展" valuePropName="checked">
                <Switch checkedChildren="开" unCheckedChildren="关" />
              </Form.Item>
            </Col>
          </Row>
        </Form>

        <Divider orientation="left">Benchmark 数据</Divider>
        <Row gutter={24}>
          <Col xs={24} md={12}>
            <Space style={{ marginBottom: 8 }}>
              <Select
                placeholder="选择已保存的数据集"
                allowClear
                style={{ minWidth: 200 }}
                loading={savedDatasetsLoading}
                value={selectedDatasetId ?? undefined}
                onChange={(id) => {
                  setSelectedDatasetId(id ?? null)
                  if (id) loadDatasetById(id)
                  else {
                    setBenchmarkItems([])
                    setBenchmarkJson('')
                  }
                }}
                options={savedDatasets.map((d) => ({ label: `${d.name}（${d.items?.length ?? 0} 条）`, value: d.id }))}
              />
              <Upload accept=".json" showUploadList={false} beforeUpload={handleUploadFile}>
                <Button icon={<UploadOutlined />}>上传 JSON</Button>
              </Upload>
              <Button type="link" icon={<SaveOutlined />} onClick={() => setSaveModalVisible(true)}>
                保存为数据集
              </Button>
              <Button type="link" icon={<UnorderedListOutlined />} onClick={() => setManageDatasetsVisible(true)}>
                管理数据集
              </Button>
            </Space>
            <Input.TextArea
              placeholder={BENCHMARK_JSON_EXAMPLE}
              value={benchmarkJson}
              onChange={(e) => {
                setBenchmarkJson(e.target.value)
                setSelectedDatasetId(null)
              }}
              rows={10}
              style={{ fontFamily: 'monospace', fontSize: 12 }}
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              格式：JSON 数组，每项含 query（字符串）和 relevant_chunk_ids（数字数组，标准答案块 id）
            </Typography.Text>
          </Col>
        </Row>

        <div style={{ marginTop: 16 }}>
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            loading={running}
            onClick={handleRun}
          >
            运行评测
          </Button>
        </div>
      </Card>

      {result && (
        <Card title="评测结果" className="app-card-3d app-animate-in" style={{ marginBottom: 24 }}>
          {isAllZero && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message="召回率与 MRR 均为 0 的常见原因"
              description={
                <>
                  <p style={{ marginBottom: 8 }}>
                    <strong>Benchmark 中的 relevant_chunk_ids 与知识库中分块的真实 ID 不一致。</strong>
                    评测时用「检索到的 chunk ID」与「标准答案 ID」做匹配；若你用的是示例里的 1、2，而库里该文档的分块 ID 实际是别的数字（如 47、48），就会全部不匹配。
                  </p>
                  <p style={{ margin: 0 }}>
                    请到 <strong>知识库 → 对应文件 → 查看分块</strong>，确认每条分块的 <strong>ID</strong>，把 benchmark JSON 里的
                    <code style={{ margin: '0 4px', padding: '2px 6px', background: 'var(--app-bg-muted)' }}>relevant_chunk_ids</code>
                    改成这些真实 ID 后再重新评测。下方表格中「标准答案 ID」与「检索到的 ID(前5)」可帮助核对是否一致。
                  </p>
                </>
              }
            />
          )}
          <Row gutter={24} style={{ marginBottom: 24 }}>
            <Col xs={12} sm={6}>
              <Statistic
                title={
                  <Space size={4}>
                    Recall@1
                    <Tooltip title={METRIC_TIPS.recall_at_1}>
                      <QuestionCircleOutlined style={{ color: 'var(--app-text-secondary)', cursor: 'help' }} />
                    </Tooltip>
                  </Space>
                }
                value={result.metrics?.recall_at_1 != null ? (result.metrics.recall_at_1 * 100).toFixed(1) : '-'}
                suffix="%"
              />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic
                title={
                  <Space size={4}>
                    Recall@5
                    <Tooltip title={METRIC_TIPS.recall_at_5}>
                      <QuestionCircleOutlined style={{ color: 'var(--app-text-secondary)', cursor: 'help' }} />
                    </Tooltip>
                  </Space>
                }
                value={result.metrics?.recall_at_5 != null ? (result.metrics.recall_at_5 * 100).toFixed(1) : '-'}
                suffix="%"
              />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic
                title={
                  <Space size={4}>
                    Recall@10
                    <Tooltip title={METRIC_TIPS.recall_at_10}>
                      <QuestionCircleOutlined style={{ color: 'var(--app-text-secondary)', cursor: 'help' }} />
                    </Tooltip>
                  </Space>
                }
                value={result.metrics?.recall_at_10 != null ? (result.metrics.recall_at_10 * 100).toFixed(1) : '-'}
                suffix="%"
              />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic
                title={
                  <Space size={4}>
                    Recall@20
                    <Tooltip title={METRIC_TIPS.recall_at_20}>
                      <QuestionCircleOutlined style={{ color: 'var(--app-text-secondary)', cursor: 'help' }} />
                    </Tooltip>
                  </Space>
                }
                value={result.metrics?.recall_at_20 != null ? (result.metrics.recall_at_20 * 100).toFixed(1) : '-'}
                suffix="%"
              />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic
                title={
                  <Space size={4}>
                    MRR
                    <Tooltip title={METRIC_TIPS.mrr}>
                      <QuestionCircleOutlined style={{ color: 'var(--app-text-secondary)', cursor: 'help' }} />
                    </Tooltip>
                  </Space>
                }
                value={result.metrics?.mrr != null ? result.metrics.mrr.toFixed(3) : '-'}
              />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic
                title={
                  <Space size={4}>
                    样本数
                    <Tooltip title={METRIC_TIPS.num_queries}>
                      <QuestionCircleOutlined style={{ color: 'var(--app-text-secondary)', cursor: 'help' }} />
                    </Tooltip>
                  </Space>
                }
                value={result.metrics?.num_queries ?? 0}
              />
            </Col>
          </Row>
          <Table
            size="small"
            rowKey={(r, i) => `${i}-${r.query?.slice(0, 20)}`}
            columns={columns}
            dataSource={result.details || []}
            pagination={{ pageSize: 10, showSizeChanger: true }}
            scroll={{ x: 800 }}
          />
        </Card>
      )}

      <Modal
        title="保存为 Benchmark 数据集"
        open={saveModalVisible}
        onCancel={() => setSaveModalVisible(false)}
        onOk={handleSaveBenchmark}
        confirmLoading={saveLoading}
        okText="保存"
      >
        <Form form={saveForm} layout="vertical" initialValues={{ name: '', description: '', knowledge_base_id: undefined }}>
          <Form.Item name="name" label="数据集名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="例如：产品文档 QA 集" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={2} placeholder="可选" />
          </Form.Item>
          <Form.Item name="knowledge_base_id" label="关联知识库">
            <Select
              placeholder="可选，便于筛选"
              allowClear
              options={knowledgeBases.map((kb) => ({ label: kb.name, value: kb.id }))}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="管理数据集"
        open={manageDatasetsVisible}
        onCancel={() => setManageDatasetsVisible(false)}
        footer={null}
        width={560}
      >
        {savedDatasetsLoading ? (
          <div style={{ padding: 24, textAlign: 'center' }}>加载中…</div>
        ) : savedDatasets.length === 0 ? (
          <Typography.Text type="secondary">暂无已保存的数据集</Typography.Text>
        ) : (
          <List
            dataSource={savedDatasets}
            renderItem={(item) => (
              <List.Item
                actions={[
                  <Popconfirm
                    key="del"
                    title="确定删除该数据集？"
                    onConfirm={() => handleDeleteDataset(item.id)}
                  >
                    <Button
                      type="text"
                      danger
                      size="small"
                      icon={<DeleteOutlined />}
                      loading={deletingId === item.id}
                    >
                      删除
                    </Button>
                  </Popconfirm>,
                ]}
              >
                <List.Item.Meta
                  title={item.name}
                  description={
                    <>
                      {item.description && <span>{item.description} · </span>}
                      <span>{item.items?.length ?? 0} 条样本</span>
                      {item.knowledge_base_id != null && (
                        <span> · 关联知识库 ID: {item.knowledge_base_id}</span>
                      )}
                    </>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Modal>

    </div>
  )
}
