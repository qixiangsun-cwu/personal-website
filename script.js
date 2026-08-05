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

    // ---------- 滚动渐入动画 (IntersectionObserver) ----------
    if (!prefersReducedMotion) {
        const revealEls = document.querySelectorAll(
            '.glass-card, .section-header, .hero-content, .timeline-item, .benefit-item'
        );

        revealEls.forEach(function(el, i) {
            el.classList.add('reveal');
            // 交错延迟：每 5 个一组轮换
            const delayClass = 'reveal-delay-' + ((i % 3) + 1);
            el.classList.add(delayClass);
        });

        const observer = new IntersectionObserver(function(entries) {
            entries.forEach(function(entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                }
            });
        }, {
            threshold: 0.08,
            rootMargin: '0px 0px -40px 0px'
        });

        revealEls.forEach(function(el) { observer.observe(el); });
    }

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

    // ---------- 下载按钮交互 ----------
    document.querySelectorAll('.download-pending').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            btn.style.animation = 'none';
            btn.offsetHeight; // 触发回流
            btn.style.animation = 'shake 0.4s ease';
        });
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
    var sections = document.querySelectorAll('section[id]');
    var navLinks = document.querySelectorAll('.nav-links a');

    if (sections.length && navLinks.length) {
        var sectionObserver = new IntersectionObserver(function(entries) {
            entries.forEach(function(entry) {
                if (entry.isIntersecting) {
                    var id = entry.target.getAttribute('id');
                    navLinks.forEach(function(link) {
                        link.style.color = '';
                        link.style.fontWeight = '';
                        if (link.getAttribute('href') === '#' + id) {
                            link.style.color = 'var(--color-ink)';
                            link.style.fontWeight = '500';
                        }
                    });
                }
            });
        }, { threshold: 0.25 });

        sections.forEach(function(section) { sectionObserver.observe(section); });
    }

    // ---------- AI 新闻加载 ----------
    loadNews();

    // ---------- 历史新闻折叠 ----------
    var archiveToggle = document.getElementById('archiveToggle');
    var archiveBody = document.getElementById('newsArchiveBody');
    var archiveArrow = document.querySelector('.archive-arrow');

    if (archiveToggle) {
        archiveToggle.addEventListener('click', function() {
            var isVisible = archiveBody.style.display !== 'none';
            archiveBody.style.display = isVisible ? 'none' : 'block';
            if (archiveArrow) {
                archiveArrow.style.transform = isVisible ? '' : 'rotate(180deg)';
            }
        });
    }

    // ---------- 访问统计 ----------
    recordVisit();
});

// ---------- 加载AI新闻 ----------
async function loadNews() {
    try {
        var response = await fetch('ai-news.json');
        if (!response.ok) throw new Error('加载失败');
        var data = await response.json();

        var dateEl = document.getElementById('newsUpdateDate');
        if (dateEl) dateEl.textContent = '更新于 ' + data.updated;

        var currentList = document.getElementById('newsCurrentList');
        if (currentList && data.current && data.current.length > 0) {
            currentList.innerHTML = data.current.map(createNewsItem).join('');
        } else if (currentList) {
            currentList.innerHTML = '<p class="news-empty-hint">暂无新闻，今日更新中。</p>';
        }

        var archiveList = document.getElementById('newsArchiveList');
        var emptyHint = document.getElementById('newsEmptyHint');
        if (archiveList && data.archive && data.archive.length > 0) {
            archiveList.innerHTML = data.archive.map(createNewsItem).join('');
        } else if (archiveList && emptyHint) {
            emptyHint.style.display = 'block';
        }
    } catch (e) {
        var currentList = document.getElementById('newsCurrentList');
        if (currentList) {
            currentList.innerHTML = '<p class="news-empty-hint">新闻加载中，请稍后刷新。</p>';
        }
    }
}

function createNewsItem(item) {
    return '\n        <a href="' + item.url + '" target="_blank" rel="noopener" class="news-item">\n            <div class="news-item-content">\n                <h4 class="news-item-title">' + item.title + '</h4>\n                <p class="news-item-summary">' + item.summary + '</p>\n            </div>\n            <div class="news-item-meta">\n                <span class="news-item-source">' + item.source + '</span>\n                <span class="news-item-date">' + item.date + '</span>\n            </div>\n        </a>\n    ';
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

// ---------- 抖动动画 ----------
(function() {
    var style = document.createElement('style');
    style.textContent = '@keyframes shake { 0%, 100% { transform: translateX(0); } 25% { transform: translateX(-4px); } 75% { transform: translateX(4px); } }';
    document.head.appendChild(style);
})();