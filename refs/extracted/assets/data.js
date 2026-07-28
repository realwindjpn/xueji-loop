// ZeroXCore 真实 mock 数据（源自 apps/web/lib/mock-data.ts，正文已 HTML 化）
window.ZX_DATA = {
 "posts": [
  {
   "slug": "rust-async-runtime",
   "title": "Rust async 运行时的心智模型",
   "date": "2026-07-27",
   "cat": "dev",
   "catName": "开发",
   "starred": true,
   "pinned": true,
   "minutes": 2,
   "excerpt": "异步运行时的心智模型 Rust 的 async/await 与其他语言不同，它不自带运行时，需要选择 executor（tokio / asyncstd / smol）。 Fu……",
   "html": "<h2>异步运行时的心智模型</h2>\n<p>Rust 的 async/await 与其他语言不同，它不自带运行时，需要选择 executor（tokio / async-std / smol）。</p>\n<h3>Future 是惰性的</h3>\n<p>与 JS Promise 不同，Rust Future 创建后不会自动执行，必须被 poll 才会推进。这意味着：</p>\n<pre><code class=\"lang-rust\">let future = async { 42 };\n// 上面这行不执行任何东西\nlet result = future.await; // 这里才真正运行</code></pre>\n<h3>Waker 与调度</h3>\n<p>每个 Future 被挂起时，runtime 会注册一个 Waker。当 I/O 就绪时，epoll/kqueue 触发 Waker，executor 重新 poll 该 Future。</p>\n<p>这套机制是零成本的——没有 GC、没有堆分配（除非你用 Box::pin），编译器把 async/await 编译成状态机。</p>\n<h3>实践建议</h3>\n<ul><li>优先用 tokio，生态最成熟</li><li>不要在 async 上下文里调用 std::sync::Mutex，用 tokio::sync::Mutex</li><li>spawn 的任务默认在 &#x27;static 生命周期上运行，需要借用就用 scope</li></ul>"
  },
  {
   "slug": "k8s-crashloopbackoff",
   "title": "K8s Pod 卡在 CrashLoopBackOff 的排查指南",
   "date": "2026-07-26",
   "cat": "dev",
   "catName": "开发",
   "starred": false,
   "pinned": true,
   "minutes": 2,
   "excerpt": "Pod 卡在 CrashLoopBackOff 排查链路：kubectl describe pod → 看 Events 和 Last State → kubectl logs……",
   "html": "<h2>Pod 卡在 CrashLoopBackOff</h2>\n<p>排查链路：kubectl describe pod → 看 Events 和 Last State → kubectl logs --previous → 看退出码和错误日志。</p>\n<p>常见的 6 个原因：</p>\n<ol><li><strong>容器启动命令错误</strong> — command / args 配置不对</li><li><strong>依赖未就绪</strong> — 连了不存在的 ConfigMap / Secret / 数据库</li><li><strong>探针失败</strong> — readiness/liveness probe 超时或返回非 200</li><li><strong>资源不足</strong> — OOMKilled，需要调 memory limit</li><li><strong>权限问题</strong> — ServiceAccount 缺少 RBAC 权限</li><li><strong>镜像问题</strong> — 拉取失败或 entrypoint 崩溃</li></ol>\n<h3>最快的定位方式</h3>\n<pre><code class=\"lang-bash\">kubectl describe pod &lt;pod-name&gt; | grep -A5 &quot;Last State&quot;\nkubectl logs &lt;pod-name&gt; --previous --tail=50</code></pre>\n<p>看到 Exit Code 是 137 就是 OOMKilled，是 1 就是应用自身 panic。</p>"
  },
  {
   "slug": "first-triathlon",
   "title": "第一次尝试标铁：1.5k 游 + 40k 骑 + 10k 跑",
   "date": "2026-07-25",
   "cat": "daily",
   "catName": "日常",
   "starred": true,
   "pinned": false,
   "minutes": 2,
   "excerpt": "第一次尝试标铁 报名了本地铁三俱乐部的标准距离赛事：1.5km 游泳 + 40km 骑行 + 10km 跑步。 赛前 泳池训练了三个月，但开放水域完全是另一回事。第一次下湖时，……",
   "html": "<h2>第一次尝试标铁</h2>\n<p>报名了本地铁三俱乐部的标准距离赛事：1.5km 游泳 + 40km 骑行 + 10km 跑步。</p>\n<h3>赛前</h3>\n<p>泳池训练了三个月，但开放水域完全是另一回事。第一次下湖时，没有池底的黑线、没有泳道绳、水温 22 度让呼吸节奏全乱了。</p>\n<h3>比赛日</h3>\n<p>早上 6 点到场，水温 20 度，穿胶衣下水的那一刻确实冷，但游起来就好多了。</p>\n<p>骑行段是风景最好的一段，沿着海岸线骑行，40km 用了 1 小时 12 分。跑步段最难熬，从车上下来腿完全是木的，前 2km 像在踩棉花。</p>\n<p>最终完赛时间 2:47:33，比预期快了 3 分钟。</p>\n<h3>下一步</h3>\n<p>下半年目标是半程大铁（70.3），游泳需要加强。</p>"
  },
  {
   "slug": "cesium-heatmap",
   "title": "用 CesiumJS 在三维地球上渲染热力图",
   "date": "2026-07-23",
   "cat": "dev",
   "catName": "开发",
   "starred": false,
   "pinned": false,
   "minutes": 2,
   "excerpt": "用 CesiumJS 实现热力图 项目需求是在三维地球表面渲染全球城市热力分布。 方案对比 1. Primitive + 自定义着色器 — 性能最好，但开发成本高 2. Hea……",
   "html": "<h2>用 CesiumJS 实现热力图</h2>\n<p>项目需求是在三维地球表面渲染全球城市热力分布。</p>\n<h3>方案对比</h3>\n<ol><li><strong>Primitive + 自定义着色器</strong> — 性能最好，但开发成本高</li><li><strong>HeatmapImageryProvider</strong> — 基于热力图库生成瓦片，简单但不够灵活</li><li><strong>Entity + Billboard</strong> — 适合少量点位，大规模性能差</li></ol>\n<p>最终选了方案 1，用 WebGL 计算热力分布并直接渲染。</p>\n<h3>核心着色器</h3>\n<p>顶点着色器把经纬度转 WebMercator，片段着色器按高斯核函数计算密度，最后用渐变色映射到 RGBA。</p>\n<pre><code class=\"lang-glsl\">float intensity = 0.0;\nfor (int i = 0; i &lt; MAX_POINTS; i++) {\n  vec2 d = v_coord - u_points[i];\n  intensity += exp(-dot(d, d) * u_radius);\n}\ngl_FragColor = texture2D(u_gradient, vec2(intensity, 0.5));</code></pre>\n<p>性能：10000 个点在 RTX 3060 上稳定 60fps。</p>"
  },
  {
   "slug": "zeroxcore-architecture",
   "title": "ZeroXCore 架构总览",
   "date": "2026-07-21",
   "cat": "dev",
   "catName": "开发",
   "starred": false,
   "pinned": false,
   "minutes": 2,
   "excerpt": "ZeroXCore 架构总览 ZeroXCore 是一个个人博客 + 本地云端存储系统，端侧为 Web 和 Android。 数据流 PostgreSQL 是权威数据源。Web……",
   "html": "<h2>ZeroXCore 架构总览</h2>\n<p>ZeroXCore 是一个个人博客 + 本地云端存储系统，端侧为 Web 和 Android。</p>\n<h3>数据流</h3>\n<p>PostgreSQL 是权威数据源。Web 后台和 Android 都是数据管理端，通过 <code>/api/sync/*</code> 协议交换数据。</p>\n<h3>同步协议</h3>\n<ul><li><code>POST /api/sync/bootstrap</code> — 首次全量拉取</li><li><code>POST /api/sync/changes</code> — 增量变更拉取</li><li><code>POST /api/sync/push</code> — 本地变更推送</li><li><code>POST /api/sync/media</code> — 媒体上传</li><li><code>POST /api/sync/conflicts/:id/resolve</code> — 冲突解决</li></ul>\n<p>Android 端用 Room(SQLCipher) 做加密本地副本，靠 WorkManager 做后台同步队列。</p>\n<h3>部署</h3>\n<p>轻量服务器 + 文件系统媒体存储，不上云。带宽预算闸门控制每月出口流量。</p>"
  },
  {
   "slug": "refactoring-strategy",
   "title": "重构：从 12% 覆盖率到 78% 的三个月",
   "date": "2026-07-18",
   "cat": "dev",
   "catName": "开发",
   "starred": true,
   "pinned": false,
   "minutes": 2,
   "excerpt": "重构：从混乱到清晰 接手了一个三年期的项目，测试覆盖率 12%，圈复杂度最高 47。 策略 不搞大爆炸式重写。按以下顺序： 1. 补测试 — 先给核心路径写集成测试，建立安全网……",
   "html": "<h2>重构：从混乱到清晰</h2>\n<p>接手了一个三年期的项目，测试覆盖率 12%，圈复杂度最高 47。</p>\n<h3>策略</h3>\n<p>不搞大爆炸式重写。按以下顺序：</p>\n<ol><li><strong>补测试</strong> — 先给核心路径写集成测试，建立安全网</li><li><strong>提取模块</strong> — 把 God Object 拆成职责清晰的模块</li><li><strong>消除重复</strong> — 提取公共抽象，但不提前抽象</li><li><strong>改善命名</strong> — 好名字比注释更重要</li></ol>\n<h3>关键原则</h3>\n<ul><li>每次重构后测试必须全绿</li><li>一个 PR 只做一件事</li><li>不改变外部行为</li><li>先让代码能测，再让代码更好</li></ul>\n<p>三个月后覆盖率到 78%，最复杂函数降到 15。最重要的是——新功能开发速度明显变快了。</p>"
  },
  {
   "slug": "night-run-4am",
   "title": "凌晨四点的城市",
   "date": "2026-07-16",
   "cat": "daily",
   "catName": "日常",
   "starred": false,
   "pinned": false,
   "minutes": 2,
   "excerpt": "凌晨四点的城市 失眠起来跑步，凌晨四点的城市出乎意料地安静。 路灯把影子拉得很长，空气凉而干净。跑过空无一人的商业街、打烊的便利店、还在营业的 24 小时药房。 跑到 5km ……",
   "html": "<h2>凌晨四点的城市</h2>\n<p>失眠起来跑步，凌晨四点的城市出乎意料地安静。</p>\n<p>路灯把影子拉得很长，空气凉而干净。跑过空无一人的商业街、打烊的便利店、还在营业的 24 小时药房。</p>\n<p>跑到 5km 时天开始亮了。清洁工开始扫街，早点摊开始支锅。城市从寂静到喧闹，这个过程比跑步本身更有意思。</p>\n<p>回到家时 6 点，正好赶上日出。</p>\n<p>跑了 8.3km，配速 5&#x27;42&quot;。</p>"
  },
  {
   "slug": "film-scanning-workflow",
   "title": "胶片扫描工作流：Gold 200 + EPSON V850",
   "date": "2026-07-13",
   "cat": "media",
   "catName": "影像",
   "starred": false,
   "pinned": false,
   "minutes": 2,
   "excerpt": "胶片扫描工作流 手冲了三卷柯达 Gold 200，用 EPSON V850 扫描。 扫描设置 分辨率：3200 dpi（35mm 胶片够用） 色彩管理：sRGB IEC6196……",
   "html": "<h2>胶片扫描工作流</h2>\n<p>手冲了三卷柯达 Gold 200，用 EPSON V850 扫描。</p>\n<h3>扫描设置</h3>\n<ul><li>分辨率：3200 dpi（35mm 胶片够用）</li><li>色彩管理：sRGB IEC61966-2.1</li><li>ICE：开启（除尘去划痕）</li><li>8 bit 足够，不追求 16 bit</li></ul>\n<h3>后期</h3>\n<p>SilverFast 扫完导出 TIFF，再进 Lightroom 做微调：</p>\n<ul><li>白平衡：偏暖一点，Gold 200 本身就偏黄</li><li>阴影：提一点，暗部细节不够</li><li>颗粒：不降噪，保留胶片感</li></ul>\n<h3>感受</h3>\n<p>胶片的色调确实有「氛围感」——那种不是靠滤镜能复刻的过渡。但也别神化，数码 RAW 后期空间更大。</p>\n<p>关键不在器材，在于按快门前多想一秒。</p>"
  }
 ],
 "cats": [
  {
   "slug": "daily",
   "name": "日常"
  },
  {
   "slug": "dev",
   "name": "开发"
  },
  {
   "slug": "media",
   "name": "影像"
  }
 ],
 "stats": {
  "posts": 8,
  "records": 666,
  "days": 120
 }
};
