-- 召回率评测：Benchmark 数据集表
-- 若使用 Base.metadata.create_all 启动应用，此表会自动创建；仅在未自动建表时手动执行本脚本。

-- MySQL
CREATE TABLE IF NOT EXISTS benchmark_datasets (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  knowledge_base_id INT NULL,
  name VARCHAR(128) NOT NULL,
  description TEXT NULL,
  items TEXT NOT NULL COMMENT 'JSON: [{"query":"...","relevant_chunk_ids":[1,2,3]}]',
  created_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  INDEX idx_benchmark_datasets_user_id (user_id),
  INDEX idx_benchmark_datasets_kb_id (knowledge_base_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_bases(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- PostgreSQL（若使用 PostgreSQL，注释上方 MySQL 块，取消下方注释）
/*
CREATE TABLE IF NOT EXISTS benchmark_datasets (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  knowledge_base_id INTEGER NULL REFERENCES knowledge_bases(id) ON DELETE SET NULL,
  name VARCHAR(128) NOT NULL,
  description TEXT NULL,
  items TEXT NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_benchmark_datasets_user_id ON benchmark_datasets(user_id);
CREATE INDEX IF NOT EXISTS idx_benchmark_datasets_kb_id ON benchmark_datasets(knowledge_base_id);
*/
