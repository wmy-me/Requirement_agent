"""目标命名空间：workers —— 后台任务与消费循环。

当前实际代码在 src/infrastructure/worker/（tasks / outbox / consumer）与 apps/worker/。
子批次 2 迁移 worker 入口、子批次 4 迁移基础设施内容；迁移期间保持旧路径兼容。
"""
