CREATE TABLE IF NOT EXISTS mcp_tasks (
    task_id VARCHAR(64) NOT NULL COMMENT '任务ID',
    status VARCHAR(32) NOT NULL COMMENT '任务状态',
    status_message TEXT NULL COMMENT '任务状态说明',
    created_at DATETIME(6) NOT NULL COMMENT '任务创建时间',
    last_updated_at DATETIME(6) NOT NULL COMMENT '任务最后更新时间',
    ttl_ms INT NULL COMMENT '任务TTL（毫秒）',
    poll_interval_ms INT NOT NULL COMMENT '客户端建议轮询间隔（毫秒）',
    expires_at DATETIME(6) NULL COMMENT '任务过期时间',
    result_json JSON NULL COMMENT '任务结果JSON',
    PRIMARY KEY (task_id),
    KEY idx_mcp_tasks_expires_at (expires_at),
    KEY idx_mcp_tasks_created_at_task_id (created_at, task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='MCP任务状态表';
