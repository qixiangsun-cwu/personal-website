// ============================================
// 孙琪翔个人网站 - 交互脚本
// taste-skill + impeccable · 2026-08-05
// ============================================

document.addEventListener('DOMContentLoaded', function() {

    // 检测 reduced-motion 偏好
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ---------- 导航栏滚动效果 ----------
    const navbar = document.getElementById('navbar');
    if (navbar) {
        window.addEventListener('scroll', function() {
            if (window.pageYOffset > 20) {
                navbar.classList.add('scrolled');
            } else {
                navbar.classList.remove('scrolled');
            }
        }, { passive: true });
    }

    // ---------- 移动端导航菜单 ----------
    const navToggle = document.getElementById('navToggle');
    const navMobile = document.getElementById('navMobile');
    if (navToggle && navMobile) {
        const navIcon = navToggle.querySelector('i');

        function setMenu(open) {
            navMobile.classList.toggle('open', open);
            navToggle.classList.toggle('active', open);
            navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            navToggle.setAttribute('aria-label', open ? '关闭菜单' : '打开菜单');
            if (navIcon) {
                navIcon.className = open ? 'ph ph-x' : 'ph ph-list';
            }
        }

        navToggle.addEventListener('click', function() {
            setMenu(!navMobile.classList.contains('open'));
        });

        navMobile.querySelectorAll('a').forEach(function(link) {
            link.addEventListener('click', function() {
                setMenu(false);
            });
        });

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && navMobile.classList.contains('open')) {
                setMenu(false);
                navToggle.focus();
            }
        });
    }

    // ---------- 入场动效（渐进增强） ----------
    // 只有 JS 就绪才挂 .js-motion，保证禁用 JS 时内容完整可见
    if (!prefersReducedMotion) {
        document.documentElement.classList.add('js-motion');
    }
    initReveal(prefersReducedMotion);

    // ---------- 平滑滚动 ----------
    document.querySelectorAll('a[href^="#"]').forEach(function(anchor) {
        anchor.addEventListener('click', function(e) {
            var targetId = this.getAttribute('href');
            if (targetId === '#') return;

            var target = document.querySelector(targetId);
            if (target) {
                e.preventDefault();
                var offset = 68;
                var targetPosition = target.getBoundingClientRect().top + window.pageYOffset - offset;
                window.scrollTo({ top: targetPosition, behavior: 'smooth' });
            }
        });
    });

    // ---------- 待补充项：声明式禁用 ----------
    // 原实现点击时注入 shake 抖动关键帧，但按钮仍可被键盘触发、也无 aria-disabled。
    // 现在只声明状态；不可交互由 CSS（pointer-events + aria-disabled）保证。
    document.querySelectorAll('.download-pending').forEach(function(btn) {
        btn.setAttribute('aria-disabled', 'true');
    });

    // ---------- 视差背景效果 (pointer-driven, passive) ----------
    if (!prefersReducedMotion) {
        var blobs = document.querySelectorAll('.bg-blob');
        var mouseX = 0, mouseY = 0;
        var blobX = 0, blobY = 0;

        document.addEventListener('mousemove', function(e) {
            mouseX = (e.clientX / window.innerWidth - 0.5) * 24;
            mouseY = (e.clientY / window.innerHeight - 0.5) * 24;
        }, { passive: true });

        function animateBlobs() {
            blobX += (mouseX - blobX) * 0.025;
            blobY += (mouseY - blobY) * 0.025;

            blobs.forEach(function(blob, index) {
                var factor = (index + 1) * 0.45;
                blob.style.transform = 'translate(' + (blobX * factor) + 'px, ' + (blobY * factor) + 'px)';
            });

            requestAnimationFrame(animateBlobs);
        }

        animateBlobs();
    }

    // ---------- 导航高亮当前 section ----------
    // 原实现用 IntersectionObserver threshold 0.25：视口高 900px 时
    // 没有任何 section 能占满 25%（hero 仅 591px），导致顶部经常无高亮，
    // 且用内联 style 改色、不写 aria-current、与 hover 下划线不是同一套语言。
    // 现改为按「距导航栏最近的 section」计算，纯类切换 + aria-current。
    initNavHighlight();

    // ---------- AI 新闻加载 ----------
    loadNews();

    // ---------- 工具区检索与筛选 ----------
    initToolFilter();

    // ---------- 访问统计 ----------
    recordVisit();
});

// ---------- 入场动效 ----------
// 原实现给每一个 .glass-card（60+ 个）单独加 reveal + 3 档循环延迟，
// 导致工具区一滚进来 60 张卡同时淡入：既无分组意义，也浪费合成层。
// 现在改为「按区块入场」：区块整体位移，区块内元素用短间隔错开。
function initReveal(reduced) {
    var docEl = document.documentElement;

    if (reduced || typeof window.IntersectionObserver !== 'function') {
        docEl.classList.add('reveal-ready');
        return;
    }

    var targets = document.querySelectorAll('.section, .hero-content');

    targets.forEach(function(section) {
        var probe = section.matches('.hero-content')
            ? section
            : section.querySelector('.section-header') || section;

        if (probe.closest('.section, .hero-content') !== section) return;

        section.classList.add('reveal');

        // 区块内首屏可见的少数元素做轻量错开（最多 4 个）
        var members = section.querySelectorAll(
            '.glass-card, .timeline-item, .benefit-item, .module-card, .course-card, .download-card, .tool-card'
        );
        var n = 0;
        members.forEach(function(el) {
            if (n >= 4) return;
            if (el.closest('.section, .hero-content') !== section) return;
            el.style.setProperty('--reveal-delay', (0.06 * (++n)) + 's');
            el.classList.add('reveal-child');
        });
    });

    var observer = new IntersectionObserver(function(entries) {
        entries.forEach(function(entry) {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                observer.unobserve(entry.target);
            }
        });
    }, {
        threshold: 0.06,
        rootMargin: '0px 0px -60px 0px'
    });

    targets.forEach(function(el) { observer.observe(el); });
}

// ---------- 导航当前区块 ----------
function initNavHighlight() {
    var sections = Array.prototype.slice.call(document.querySelectorAll('section[id]'));
    var navLinks = Array.prototype.slice.call(document.querySelectorAll('.nav-links a'));
    if (!sections.length || !navLinks.length) return;

    var NAVH = 62;         // 导航栏高度 + 余量
    var appliedId = null;  // 实际已写入的链接（注意不是「当前区块 id」）
    var ticking = false;

    // 只高亮「有对应导航链接」的区块。#hero 没有导航项，
    // 若直接用最近区块 id 去匹配，顶部会没有任何高亮 —— 改为此处直接过滤。
    var linked = sections.filter(function(sec) {
        var id = sec.getAttribute('id');
        return navLinks.some(function(a) { return a.getAttribute('href') === '#' + id; });
    });
    if (!linked.length) return;

    function update() {
        ticking = false;
        var best = null;
        var bestDist = Infinity;

        linked.forEach(function(sec) {
            // section 顶边到导航栏下沿的距离；负数表示已经滚过头
            var top = sec.getBoundingClientRect().top - NAVH;
            var dist = top <= 0 ? -top : top;
            if (dist < bestDist) {
                bestDist = dist;
                best = sec;
            }
        });

        // 滚到页面最底部时，强制命中最后一个有导航的区块
        var atBottom = (window.innerHeight + window.pageYOffset) >= (document.documentElement.scrollHeight - 4);
        if (atBottom) best = linked[linked.length - 1];

        if (!best) return;
        var id = best.getAttribute('id');
        if (id === appliedId) return;
        appliedId = id;

        navLinks.forEach(function(link) {
            var match = link.getAttribute('href') === '#' + id;
            link.classList.toggle('is-current', match);
            if (match) {
                link.setAttribute('aria-current', 'true');
            } else {
                link.removeAttribute('aria-current');
            }
        });
    }

    function onScroll() {
        if (ticking) return;
        ticking = true;
        window.requestAnimationFrame(update);
    }

    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    update();
}

// ---------- 加载AI新闻 ----------
async function loadNews() {
    try {
        var response = await fetch('ai-news.json');
        if (!response.ok) throw new Error('加载失败');
        var data = await response.json();

        var dateEl = document.getElementById('newsUpdateDate');
        if (dateEl) dateEl.textContent = '更新于 ' + data.updated;

        // 合并去重 + 按日期倒序
        var allItems = dedupeNews(data.current || []).concat(dedupeNews(data.archive || []));
        allItems.sort(function(a, b) {
            return String(b.date || '').localeCompare(String(a.date || ''));
        });

        var totalEl = document.getElementById('newsTotalCount');
        if (totalEl) totalEl.textContent = allItems.length;

        var moreSub = document.getElementById('newsMoreSub');
        if (moreSub && allItems.length > 0) {
            moreSub.textContent = '共 ' + allItems.length + ' 条 · ' +
                allItems[allItems.length - 1].date + ' 至 ' + allItems[0].date;
        }

        renderNews(allItems);
    } catch (e) {
        var currentList = document.getElementById('newsCurrentList');
        if (currentList) {
            currentList.innerHTML = '<p class="news-empty-hint">新闻加载失败，请通过 GitHub Pages 访问本站或稍后刷新重试。</p>';
        }
    }
}

function dedupeNews(items) {
    var seen = {};
    var out = [];
    (items || []).forEach(function(item) {
        var t = String(item.title || '').trim();
        if (t && !seen[t]) {
            seen[t] = 1;
            out.push(item);
        }
    });
    return out;
}

var NEWS_PAGE_SIZE = 8;

// 单一列表：默认展示最新 8 条，可展开全部
function renderNews(items) {
    var listEl = document.getElementById('newsCurrentList');
    var btn = document.getElementById('newsMoreBtn');
    if (!listEl) return;

    if (!items.length) {
        listEl.innerHTML = '<p class="news-empty-hint">暂无新闻，今日更新中。</p>';
        if (btn) btn.style.display = 'none';
        return;
    }

    var expanded = false;

    function paint() {
        var shown = expanded ? items : items.slice(0, NEWS_PAGE_SIZE);
        listEl.innerHTML = shown.map(createNewsItem).join('');

        if (items.length <= NEWS_PAGE_SIZE) {
            if (btn) btn.style.display = 'none';
            return;
        }
        if (!btn) return;
        btn.style.display = '';
        btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        btn.innerHTML = expanded
            ? '<i class="ph ph-caret-up" aria-hidden="true"></i>收起'
            : '展开全部动态（' + items.length + ' 条）<i class="ph ph-caret-down" aria-hidden="true"></i>';
        if (expanded) {
            btn.classList.add('is-expanded');
        } else {
            btn.classList.remove('is-expanded');
        }
    }

    paint();

    if (btn && !btn.dataset.bound) {
        btn.dataset.bound = '1';
        btn.addEventListener('click', function() {
            expanded = !expanded;
            paint();
            if (!expanded) {
                listEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }
        });
    }
}

function createNewsItem(item) {
    var url = item.url || '#';
    var title = escapeHtml(item.title || '');
    var summary = escapeHtml(item.summary || '');
    var source = escapeHtml(item.source || '');
    var date = escapeHtml(item.date || '');
    return '\n        <a href="' + url + '" target="_blank" rel="noopener" class="news-item">\n            <div class="news-item-content">\n                <h4 class="news-item-title">' + title + '</h4>\n                <p class="news-item-summary">' + summary + '</p>\n            </div>\n            <div class="news-item-meta">\n                <span class="news-item-date">' + date + '</span>\n                <span class="news-item-source">' + source + '</span>\n            </div>\n        </a>\n    ';
}

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// ---------- 工具区：搜索 + 等级筛选 + 分类折叠 ----------
function initToolFilter() {
    var searchInput = document.getElementById('toolSearch');
    var resultEl = document.getElementById('toolResult');
    var filterBtns = Array.prototype.slice.call(document.querySelectorAll('.tool-filter'));
    var sections = Array.prototype.slice.call(document.querySelectorAll('.tool-cat'));
    if (!sections.length) return;

    var activeLevel = 'all';
    var query = '';

    function apply() {
        var q = query.trim().toLowerCase();
        var total = 0;
        var filtering = !!q || activeLevel !== 'all';

        sections.forEach(function(sec) {
            var cards = Array.prototype.slice.call(sec.querySelectorAll('.tool-card'));
            var visible = 0;

            cards.forEach(function(card) {
                var matchLevel = activeLevel === 'all' || card.dataset.level === activeLevel;
                var matchText = !q || (card.dataset.search || '').indexOf(q) !== -1;
                if (matchLevel && matchText) {
                    card.hidden = false;
                    visible++;
                } else {
                    card.hidden = true;
                }
            });

            total += visible;

            if (visible === 0) {
                sec.hidden = true;
            } else {
                sec.hidden = false;
                if (filtering) {
                    // 检索态自动展开命中分类，并记住用户原本的折叠状态
                    if (sec.dataset.userOpen === undefined) {
                        sec.dataset.userOpen = sec.open ? '1' : '0';
                    }
                    sec.open = true;
                } else if (sec.dataset.userOpen !== undefined) {
                    sec.open = sec.dataset.userOpen === '1';
                    delete sec.dataset.userOpen;
                }
            }
        });

        if (resultEl) {
            resultEl.textContent = filtering ? ('筛选出 ' + total + ' 款工具') : '';
        }
    }

    if (searchInput) {
        searchInput.addEventListener('input', function() {
            query = searchInput.value || '';
            apply();
        });
    }

    filterBtns.forEach(function(btn) {
        btn.addEventListener('click', function() {
            activeLevel = btn.dataset.level || 'all';
            filterBtns.forEach(function(b) { b.classList.toggle('is-active', b === btn); });
            apply();
        });
    });
}

// ---------- 访问统计 ----------
function recordVisit() {
    try {
        var today = new Date().toISOString().split('T')[0];
        var visits = JSON.parse(localStorage.getItem('site_visits') || '{}');

        if (!visits[today]) {
            visits[today] = { count: 0, unique: '' };
        }

        var visitData = visits[today];
        var uniqueSet = new Set(visitData.unique ? visitData.unique.split(',').filter(Boolean) : []);
        var visitorId = getVisitorId();
        uniqueSet.add(visitorId);

        visitData.count = (visitData.count || 0) + 1;
        visitData.unique = Array.from(uniqueSet).join(',');
        visits[today] = visitData;

        localStorage.setItem('site_visits', JSON.stringify(visits));
    } catch (e) {
        // 静默失败
    }
}

function getVisitorId() {
    var id = localStorage.getItem('visitor_id');
    if (!id) {
        id = 'v_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6);
        localStorage.setItem('visitor_id', id);
    }
    return id;
}
