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


CREATE TABLE IF NOT EXISTS mcp_task_recovery (
    task_id VARCHAR(64) NOT NULL COMMENT '任务ID',
    tool_name VARCHAR(128) NOT NULL COMMENT '任务对应工具名',
    tool_args_json JSON NOT NULL COMMENT '任务工具入参JSON',
    execution_state VARCHAR(32) NOT NULL COMMENT '任务执行状态',
    replayable TINYINT(1) NOT NULL DEFAULT 0 COMMENT '任务是否可重放',
    user_id VARCHAR(128) NOT NULL COMMENT '任务提交用户ID',
    tenant_id VARCHAR(128) NOT NULL COMMENT '任务提交租户ID',
    scopes_json JSON NOT NULL COMMENT '任务授权范围JSON',
    approval_grants_json JSON NOT NULL COMMENT '任务审批授权JSON',
    attempt_count INT NOT NULL DEFAULT 0 COMMENT '任务重放次数',
    last_error TEXT NULL COMMENT '最后一次执行错误',
    recovery_started_at DATETIME(6) NULL COMMENT '最近一次恢复启动时间',
    lease_owner VARCHAR(128) NULL COMMENT '恢复租约持有者',
    lease_expires_at DATETIME(6) NULL COMMENT '恢复租约过期时间',
    PRIMARY KEY (task_id),
    KEY idx_mcp_task_recovery_execution_state (execution_state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='MCP任务恢复元数据表';
