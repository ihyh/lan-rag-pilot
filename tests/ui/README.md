# 正式页面交互检查

在项目根目录运行 `npm ci --prefix tests/ui` 和 `npm test --prefix tests/ui`。需要 Node.js 24。使用 jsdom 和模拟 API 检查用户权限、授权操作、URL 导航及历史切换；不访问生产服务，不替代浏览器视觉验收。
