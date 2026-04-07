# application（用例层）

**职责**：编排单次或批量业务用例（问答、入库、评测、Agent 运行），调用 `domain` 策略与 `infrastructure` 适配器。

**依赖方向**：`application` → `domain`（接口）→ `infrastructure`（实现）；不得被 `domain` 依赖。

**当前状态**：`chat_facade.py` 委托既有 `ChatService`，行为与改造前一致，后续逐步将编排迁入本层。
