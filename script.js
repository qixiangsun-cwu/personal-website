// ============================================
// 孙琪翔个人网站 - 交互脚本
// Glassmorphism × Apple Design
// ============================================

document.addEventListener('DOMContentLoaded', function() {

    // ---------- 导航栏滚动效果 ----------
    const navbar = document.getElementById('navbar');
    let lastScroll = 0;

    window.addEventListener('scroll', function() {
        const currentScroll = window.pageYOffset;

        if (currentScroll > 20) {
            navbar.classList.add('scrolled');
        } else {
            navbar.classList.remove('scrolled');
        }

        lastScroll = currentScroll;
    });

    // ---------- 滚动渐入动画 ----------
    const observerOptions = {
        threshold: 0.1,
        rootMargin: '0px 0px -50px 0px'
    };

    const observer = new IntersectionObserver(function(entries) {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
            }
        });
    }, observerOptions);

    // 给所有 glass-card 添加渐入动画
    document.querySelectorAll('.glass-card, .section-header').forEach(el => {
        el.classList.add('fade-in');
        observer.observe(el);
    });

    // ---------- 平滑滚动（兼容旧浏览器）----------
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function(e) {
            const targetId = this.getAttribute('href');
            if (targetId === '#') return;

            const target = document.querySelector(targetId);
            if (target) {
                e.preventDefault();
                const offset = 70; // 导航栏高度
                const targetPosition = target.getBoundingClientRect().top + window.pageYOffset - offset;

                window.scrollTo({
                    top: targetPosition,
                    behavior: 'smooth'
                });
            }
        });
    });

    // ---------- 下载按钮交互 ----------
    document.querySelectorAll('.download-pending').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            // 轻微抖动提示
            this.style.animation = 'none';
            setTimeout(() => {
                this.style.animation = 'shake 0.4s ease';
            }, 10);
        });
    });

    // ---------- 视差背景效果 ----------
    const blobs = document.querySelectorAll('.bg-blob');
    let mouseX = 0, mouseY = 0;
    let blobX = 0, blobY = 0;

    document.addEventListener('mousemove', function(e) {
        mouseX = (e.clientX / window.innerWidth - 0.5) * 30;
        mouseY = (e.clientY / window.innerHeight - 0.5) * 30;
    });

    function animateBlobs() {
        blobX += (mouseX - blobX) * 0.03;
        blobY += (mouseY - blobY) * 0.03;

        blobs.forEach((blob, index) => {
            const factor = (index + 1) * 0.5;
            blob.style.transform = `translate(${blobX * factor}px, ${blobY * factor}px)`;
        });

        requestAnimationFrame(animateBlobs);
    }

    animateBlobs();

    // ---------- 导航高亮当前 section ----------
    const sections = document.querySelectorAll('section[id]');
    const navLinks = document.querySelectorAll('.nav-links a');

    const sectionObserver = new IntersectionObserver(function(entries) {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const id = entry.target.getAttribute('id');
                navLinks.forEach(link => {
                    link.style.color = '';
                    link.style.fontWeight = '';
                    if (link.getAttribute('href') === '#' + id) {
                        link.style.color = 'var(--text-primary)';
                        link.style.fontWeight = '600';
                    }
                });
            }
        });
    }, { threshold: 0.3 });

    sections.forEach(section => sectionObserver.observe(section));
});

// ---------- 抖动动画 ----------
const style = document.createElement('style');
style.textContent = `
    @keyframes shake {
        0%, 100% { transform: translateX(0); }
        25% { transform: translateX(-4px); }
        75% { transform: translateX(4px); }
    }
`;
document.head.appendChild(style);
