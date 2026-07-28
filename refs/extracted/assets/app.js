/* V4 像素 RPG —— 交互 */
(function () {
  // 星空背景
  const cv = document.getElementById('stars');
  const ctx = cv.getContext('2d');
  function resize() { cv.width = innerWidth; cv.height = innerHeight; }
  resize(); addEventListener('resize', resize);
  const stars = Array.from({ length: 60 }, () => ({
    x: Math.random() * cv.width, y: Math.random() * cv.height,
    s: Math.random() * 2 + 0.5, v: Math.random() * 0.3 + 0.05, h: Math.random() * 360,
  }));
  function tick() {
    ctx.clearRect(0, 0, cv.width, cv.height);
    stars.forEach(st => {
      st.y += st.v; if (st.y > cv.height) st.y = -2;
      ctx.fillStyle = `hsl(${st.h},70%,80%)`;
      ctx.fillRect(st.x, st.y, st.s, st.s);
    });
    requestAnimationFrame(tick);
  }
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) tick();

  // 标题屏 → 角色卡
  const start = () => {
    document.getElementById('titleScreen').hidden = true;
    document.getElementById('cardScreen').hidden = false;
    document.body.dataset.state = 'card';
  };
  document.getElementById('startBtn').addEventListener('click', start);
  document.getElementById('startBtn').addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); start(); }});
  addEventListener('keydown', e => {
    if (document.body.dataset.state === 'title' && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); start(); }
  });

  // 菜单
  document.getElementById('menu').querySelectorAll('.menu-item').forEach(it => {
    it.addEventListener('click', () => doAction(it.dataset.go));
    it.addEventListener('keydown', e => { if (e.key === 'Enter') doAction(it.dataset.go); });
  });
  function doAction(a) {
    if (a === 'posts') location.href = 'posts.html';
    if (a === 'about') { document.getElementById('cardScreen').hidden = true; document.getElementById('aboutScreen').hidden = false; document.body.dataset.state = 'about'; }
    if (a === 'back') { document.getElementById('aboutScreen').hidden = true; document.getElementById('cardScreen').hidden = false; document.body.dataset.state = 'card'; }
    if (a === 'settings') { document.querySelector('.press-start') && (document.querySelector('.press-start').textContent = '▶ 音效 · BEEP BOOP'); setTimeout(() => { document.querySelector('.press-start') && (document.querySelector('.press-start').textContent = '▶ 继续冒险 · 任务日志'); }, 1400); document.getElementById('menu').querySelector('[data-go="settings"]').textContent = '　设置 · 音效（已开）'; }
    if (a === 'quit') { document.body.dataset.state = 'title'; document.getElementById('cardScreen').hidden = true; document.getElementById('titleScreen').hidden = false; }
  }

  // 数据填充
  const D = window.ZX_DATA;
  if (D) {
    document.getElementById('statPosts').textContent = D.stats.posts;
    document.getElementById('statCats').textContent = D.cats.length;
    document.getElementById('statDays').textContent = D.stats.days;
  }
})();
