// ===== 场景详细内容数据（来源：智能体养料.docx）=====
const scenarios = {
    nightrun: {
        title: '🏃‍♀️ 夜跑安全',
        risk: '夜晚光线昏暗、能见度低，独自跑步的女孩，更容易被不怀好意的人盯上。不是说夜跑不好，只是天黑之后，世界确实会变得复杂一些。',
        source: '来源：河北省公安厅、天津长跑协会',
        tips: [
            '<strong>路线选择：</strong>选一条明亮、熟悉的路线吧，不要临时起意去跑没走过的路；但也别每天都跑同一条，偶尔换换，别让有心人摸清你的规律。',
            '<strong>逆向跑步：</strong>逆着车流跑，让开车的人能看见你，你也能看见他们的动向。',
            '<strong>让自己醒目：</strong>穿亮一点、带点荧光的衣服和跑鞋，夜里那一点点光，就是你的保护色。',
            '<strong>不带耳机：</strong>跑步时尽量别戴耳机。夜晚视觉已经减弱了，听不到车鸣和周围的动静，会很危险。实在想听，只戴一边。',
            '<strong>手机随身：</strong>手机带在身上，万一有事，能定位、能打电话。',
            '<strong>时间把控：</strong>尽量21:00前跑完，天黑之后，能不出门就不出门；非要出门，挑有路灯的大路走，不走黑路、小路。'
        ]
    },
    followed: {
        title: '🚶‍♀️ 步行回家/被尾随',
        risk: '走偏僻小巷、昏暗街道，或者太专注看手机，容易忽略身后的动静。深夜加班回家被尾随、半夜听到门外有撬锁声、家门口有陌生人站着不走——这些是独居女孩遇到最多的情况。',
        source: '来源：德州市政府安全指南、绍兴市公安局',
        tips: [
            '<strong>假装有人同行：</strong>感觉被尾随了，别慌，拨通家人或朋友的电话，或者干脆假装在打电话，大声说"你到哪儿了？三分钟，好，我在原地等你"。让身后的人以为，有人知道你在哪、在等你。',
            '<strong>往亮处、人多处走：</strong>往有光的地方去，往人多的地方去。可以跟面善的路人搭句话，假装你们认识。',
            '<strong>突然进入建筑物：</strong>路过一栋楼时，忽然加速进去，尾随的人往往会快速跟进，这时候你再慢慢退出来，对方一般就不敢继续了。',
            '<strong>按民宅门铃求助：</strong>如果对方还不走，就近找安全人员——警察、保安，或者按一户人家的门铃求助，同时明显指向身后那个人。',
            '<strong>回家时喊一句"我回来了"：</strong>独居的女孩，养成进门说一句"我回来了"的习惯。让别人以为，家里有人等你。',
            '<strong>电梯策略：</strong>晚上尽量不要和陌生可疑的男子单独同乘一部电梯。如果已经在电梯里觉得不对劲，按下最近一层按钮，换乘下一部。怀疑被跟踪时，不要按自己住的楼层，随便按一层，假装走出去；如果那人还跟着，趁电梯门没关，立刻退回电梯下楼。'
        ]
    },
    transit: {
        title: '🚇 公共交通（地铁/公交）',
        risk: '人多拥挤的时候，有人会利用这种拥挤做不好的事——偷拍、骚扰。2025年4月，成都地铁就发生过一起男子用改装设备偷拍女性裙底的事件。',
        source: '来源：成都警方通报、法治日报',
        tips: [
            '<span class="serif"><strong>发现偷拍后：</strong>在保证自己安全的前提下，可以要求对方停止并删除内容。但不要强行夺走对方的手机——手机也是他的私人物品，协商不成，找工作人员或报警。</span>',
            '<span class="serif"><strong>及时报警：</strong>发现可疑情况，及时远离并报警。越早报警，证据越可能被保住。</span>',
            '<span class="serif"><strong>证据固定：</strong>偷拍者一旦删掉照片，技术上也很难恢复，所以第一时间报警特别重要。</span>'
        ],
        case: '2025年4月，成都地铁上，一名女孩发现对面男乘客的鞋尖有疑似微型摄像头，她不动声色拍下视频，事后报警。警方很快将27岁的刘某抓获——他改装了偷拍设备，专门在拥挤车厢里对女性裙底偷拍。刘某被依法行政拘留。'
    },
    livingalone: {
        title: '🏠 独居安全',
        risk: '独居的女孩，容易成为不法分子的目标。深夜加班回家被尾随、半夜听到门外撬锁声、家门口有陌生人站着不走——这些情况，很多独居女孩都遇到过。不是你没有安全意识，而是危险真正来的时候，人很容易因为慌张而不知道该怎么办。',
        source: '来源：江苏省妇联"幸福护航"微课堂、绍兴市公安局、检察日报',
        tips: [
            '<strong>营造多人居住的假象：</strong>门外放一双男士鞋子，或者晒几件男士衣物，让不怀好意的人心生忌惮。养一条狗也很有用——陌生人在动手之前，得先想想狗叫会不会引来注意。',
            '<strong>门锁升级：</strong>入住后第一件事，换掉住宅入门的锁芯。选一把有防撬功能的智能锁。',
            '<strong>窗户限位器：</strong>既能通风，又能限制窗户打开的宽度，防止有人从窗户进来。',
            '<strong>外卖快递化名：</strong>收件人姓名用化名；在门口监控能拍到的地方收。扔快递盒之前，把名字、地址、电话划掉。',
            '<span class="serif"><strong>陌生人敲门：</strong>先通过猫眼看看是谁，不要轻易开门。隔着门大声问来意，如果回答可疑，立刻联系物业或报警。</span>',
            '<strong>定期检查偷拍设备：</strong>定期看看房间里有没有隐蔽的窃听器或针孔摄像头。'
        ],
        case: '2025年10月，成都一名合租的女孩发现男室友偷偷进入她房间，放置了偷拍设备。赵某（27岁）为寻求刺激，被依法行政拘留。'
    },
    shared: {
        title: '👥 合租安全',
        risk: '合租的室友，可能带来隐私泄露、财产安全、人身安全的隐患。合租房里被室友安放偷拍设备，是真实发生过的事。',
        source: '来源：央广网警方通报',
        tips: [
            '<strong>入住前核实：</strong>了解一下室友的基本情况，核实身份信息。',
            '<strong>个人房间门锁保持独立：</strong>贵重物品不要放在公共区域。',
            '<strong>定期检查：</strong>定期看看自己房间有没有异常设备。',
            '<span class="serif"><strong>发现偷拍立即报警：</strong>发现这类情况，第一时间报警。</span>'
        ]
    },
    stranger: {
        title: '🚪 陌生人上门',
        risk: '以"维修""查水表""送快递"的名义骗开门，是入室犯罪常见的手段。外卖和快递，是陌生人敲门的主要来源。',
        source: '来源：绍兴市公安局、江苏省妇联',
        tips: [
            '<strong>造势：</strong>如果遭遇入室抢劫，可以对歹徒说丈夫或男友就在后面，或者喊伴侣的名字，造成家里有其他人的假象，可能吓跑心虚的人。',
            '<strong>逃离：</strong>如果歹徒从门闯进来，趁门还没关，赶紧冲出门外呼救。跑到外面，就得救了。',
            '<span class="serif"><strong>躲避：</strong>如果门被堵住，赶紧找一间能反锁的房间，把门反锁，打电话报警，或者打开窗户大声呼救，等救援。</span>',
            '<strong>报信：</strong>如果不幸被控制，要想办法把求救信息传出去。',
            '<span class="serif"><strong>平时准备：</strong>贵重物品妥善保管，现金分散放；把一部闲置手机藏在自己能拿到、又不容易被发现的地方，防止歹徒抢走手机导致无法报警。</span>'
        ]
    },
    rideshare: {
        title: '🚗 网约车/出租车',
        risk: '女孩坐网约车，担心的事会比男生多——尤其是晚上。车辆信息不符、司机行为异常、路线偏离、车里藏了人——这些都不是多虑。',
        source: '来源：甘肃省女性安全防范手册、杭州网约车事件通报',
        tips: [
            '<strong>核对信息：</strong>上车前，核对车辆信息和司机信息，跟订单是不是一致。晚上不要图便宜打黑车，网约车一定核对车号，更不能和陌生男子拼车。',
            '<strong>坐后排：</strong>尽量坐后排，副驾驶是最容易被性骚扰的位置。坐司机正后方，方便观察，司机也不容易直接碰到你。',
            '<strong>分享行程：</strong>上车后，把行程分享给家人或朋友。',
            '<span class="serif"><strong>发现路线不对及时报警：</strong>坐车时不要一直低头玩手机。发现路线不对，或者觉得不对劲，及时报警。如果独自乘车遇到司机偏航，不要跳车——先通过交流确认有没有危险，从司机的言行举止判断。杭州那起网约车跳车事件里，司机两次没按导航走，女孩恐慌跳车受伤，后来警方调查发现司机没有犯罪行为。</span>'
        ],
        case: '2021年6月，杭州一名女孩坐网约车，司机两次没按导航走，她感到害怕就跳了车，受了伤。后来警方调查，没发现司机涉嫌刑事犯罪。如果当时先问一句"师傅，怎么走这条路呀"，也许就不会那么害怕了。'
    },
    driving: {
        title: '🚙 独自驾车',
        risk: '停车场、地下车库是高风险区域；一个人开车时，可能遇到陌生人拦车或者尾随。台州路桥曾发生两起专门针对独自驾车女性的地下车库抢劫案，时间都是晚上9点到10点多。',
        source: '来源：台州检察院、绍兴市公安局',
        tips: [
            '<strong>上车前检查：</strong>上车前先看看车周围和后座，确认没有异常再上车。',
            '<strong>上车后立即锁门：</strong>上车第一件事，锁门。可以设置"单独开启左前门"功能：同时按住锁车键和解锁键5秒，以后按解锁键，只有左前门能打开。',
            '<strong>车中常备防身工具：</strong>在车里固定位置放一件防身工具，平时演练一下怎么快速拿、怎么用。',
            '<span class="serif"><strong>遇险时以生命安全为先：</strong>如果不幸被控制，不要激烈反抗激怒对方。警方提示：可以假装示弱保全生命，如果对方要钱，给他一些。生命安全比财物重要得多。</span>'
        ]
    },
    hotel: {
        title: '🏨 出差住宿',
        risk: '陌生的城市、不熟悉的酒店、晚上出门——风险会叠加。2026年6月，一名女孩入住湖南衡阳某酒店第5天，发现房间假花里藏着针孔摄像头，镜头正对着床。后来她被医院诊断为"适应障碍"。',
        source: '来源：法治日报、律师建议',
        tips: [
            '<strong>入住检查：</strong>入住后，看看房间里可疑的地方——假花、空调角落、插座、烟雾报警器。',
            '<span class="serif"><strong>发现摄像头后：</strong>立刻报警，保留证据，不要自己拆。警方需要提取指纹、脚印这些证据。</span>',
            '<span class="serif"><strong>法律依据：</strong>酒店这样的公共场所，经营者有义务保障消费者安全，包括隐私安全。就算不是酒店的人装的，酒店也脱不了管理责任。</span>',
            '<strong>心理支持：</strong>遇到偷拍之后，焦虑、失眠、情绪不稳定，都是正常的反应。可以打心理热线，找专业的人聊一聊。被诊断为"适应障碍"的那位女孩，后来就是靠专业心理支持慢慢恢复的。'
        ]
    },
    fraud: {
        title: '📱 电信网络诈骗',
        risk: '<span class="serif">电信诈骗利用的是远程、不接触的方式，女孩是重点目标。公安部网安局揭示过几种"专盯女孩钱包"的骗术：婚恋交友诈骗、冒充电商客服、兼职刷单诈骗、医美贷款诈骗。</span>',
        source: '来源：公安部网安局"五要五不要"',
        tips: [
            '<strong>五要：</strong>装国家反诈中心APP，开启预警；跟家人分享防诈知识；接到陌生电话先核实（打官方客服确认）；给银行卡设转账限额；保留聊天记录、转账凭证。',
            '<strong>五不要：</strong>不要信"稳赚不赔""内部渠道"；不要透露短信验证码、银行卡密码；不要点陌生链接；不要向陌生账户转账；不要参与任何形式的刷单。',
            '<span class="serif"><strong>"教科书式反诈"参考：</strong>嘉善何女士接到冒充公检法的视频电话，她没慌——确定自己没去过南京、名下也没有那张银行卡。她假意配合，以"查询账户信息"为由挂断，立刻报警核实。电话回拨后，对方听到男声就挂了，骗局败露。记住：公安机关不会通过视频电话办案，凡是要求转账、提供验证码、开屏幕共享的，都是诈骗。</span>'
        ]
    },
    privacy: {
        title: '📷 偷拍与隐私泄露',
        risk: '偷拍在温泉酒店、合租房、地铁里都发生过，有人通过隐蔽设备偷拍，甚至形成"偷拍—引流—交易"的黑色产业链。2025年12月，天津一家医院的男护士多次在护士办公室偷拍女同事，上传网络，被抓获。',
        source: '来源：民法典、治安管理处罚法、法治日报、律师建议',
        tips: [
            '<span class="serif"><strong>法律依据：</strong>偷拍一般有三个特征：侵犯隐私、目的不正当、未经授权。按民法典，偷拍严重侵害了肖像权和隐私权，还可能构成性骚扰或寻衅滋事。2025年全国两会期间，有政协委员建议把偷拍隐私单独入罪，增设"侵犯公民隐私罪"或"偷窥罪"。</span>',
            '<strong>证据固定：</strong>保留泄露线索、骚扰记录、平台沟通记录。',
            '<span class="serif"><strong>不能强行夺手机：</strong>就算觉得对方侵犯了你的隐私，也不能强行抢手机删照片。建议协商，协商不成找工作人员或报警。</span>',
            '<span class="serif"><strong>报警处理：</strong>造成损失的，可以通过民事诉讼要求赔偿。违法行为人会被行政拘留。</span>',
            '<strong>网络平台投诉：</strong>向平台举报，要求删除侵权内容。'
        ]
    },
    cyberbully: {
        title: '💻 网络骚扰与网络暴力',
        risk: '遇到网络骚扰、诽谤、恶意P图、隐私泄露。家庭成员用网络侵权方式实施家暴，也已经被司法实践认定。',
        source: '来源：天津市和平区法院案例',
        tips: [
            '<span class="serif"><strong>法律依据：</strong>家庭成员用网络侵权方式实施家暴的，法官会用人格权禁令为受害女性筑起防护盾。《天津市妇女权益保障条例》专门增加了保护女性网络空间权益的规定。</span>'
        ],
        case: '赵晴和李强因为感情问题起诉离婚。分居后，李强长期对赵晴网络暴力，多次到她住的地方公开侮辱、损害名誉，把聊天截图发给近亲属，还在社交账号公开发布。法院认定存在家暴危险，人身安全保护令的申请符合条件，裁定禁止李强威胁、骚扰、跟踪赵晴及其父母，禁止泄露、传播赵晴隐私和个人信息。'
    }
};

// ===== 场景详情弹窗 =====
const modal = document.getElementById('scenario-modal');
const modalCloseBtn = document.querySelector('.modal-close');

document.querySelectorAll('.scenario-card').forEach(card => {
    card.setAttribute('role', 'button');
    card.setAttribute('tabindex', '0');

    const openModal = () => {
        const key = card.dataset.scenario;
        const data = scenarios[key];
        if (!data) return;

        let html = `<h3>${data.title}</h3>`;
        html += `<h4>⚠️ 风险识别</h4><p>${data.risk}</p>`;
        html += `<h4>🛡️ 应对措施</h4><ol>`;
        data.tips.forEach(tip => {
            html += `<li>${tip}</li>`;
        });
        html += `</ol>`;
        html += `<p class="modal-source">${data.source}</p>`;
        if (data.case) {
            html += `<div class="case-box"><h4>📋 真实案例</h4><p>${data.case}</p></div>`;
        }

        document.getElementById('modal-body').innerHTML = html;
        modal.classList.add('active');
        modalCloseBtn.focus();
    };

    card.addEventListener('click', openModal);
    card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            openModal();
        }
    });
});

// 关闭弹窗
const closeModal = () => modal.classList.remove('active');
modalCloseBtn.addEventListener('click', closeModal);
modal.addEventListener('click', (e) => {
    if (e.target === modal) closeModal();
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal.classList.contains('active')) closeModal();
});

// ===== FAQ 手风琴 =====
function toggleFaq(el) {
    const item = el.parentElement;
    const wasOpen = item.classList.contains('open');
    document.querySelectorAll('.faq-item').forEach(i => {
        i.classList.remove('open');
        i.querySelector('.faq-question').setAttribute('aria-expanded', 'false');
    });
    if (!wasOpen) {
        item.classList.add('open');
        el.setAttribute('aria-expanded', 'true');
    }
}

document.querySelectorAll('.faq-question').forEach(q => {
    q.setAttribute('role', 'button');
    q.setAttribute('tabindex', '0');
    q.setAttribute('aria-expanded', 'false');
    q.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            toggleFaq(q);
        }
    });
});

// ===== 热线「了解详情」手风琴：展开一个时收起其他 =====
const hotlineDetails = document.querySelectorAll('.sos-zone details');
hotlineDetails.forEach(d => {
    d.addEventListener('toggle', () => {
        if (!d.open) return;
        hotlineDetails.forEach(other => {
            if (other !== d) other.removeAttribute('open');
        });
    });
});

// =====================================================
// 邓妈妈 · 悬浮聊天挂件（对接自有后端）
// 请求：POST /api/zhian-chat  {"message": "..."}（同域 Pages Function，密钥仅存服务端）
// 返回：{"reply": "邓妈妈的回复"}
// =====================================================
(function () {
    const API_URL = '/api/zhian-chat';
    const POS_KEY = 'dmChatPos';
    const MARGIN = 12;          // 距屏幕边缘最小间距
    const DRAG_THRESHOLD = 6;   // 超过该位移判定为拖拽而非点击

    const chat = document.getElementById('dm-chat');
    const fab = document.getElementById('dm-fab');
    const panel = document.getElementById('dm-panel');
    const closeBtn = document.getElementById('dm-close');
    const messagesEl = document.getElementById('dm-messages');
    const presetsEl = document.getElementById('dm-presets');
    const inputEl = document.getElementById('dm-input');
    const sendBtn = document.getElementById('dm-send');

    if (!chat || !fab || !panel || !messagesEl || !inputEl || !sendBtn) return;

    let busy = false;

    // ---- 悬浮按钮拖拽（Pointer Events，同时支持鼠标与触屏） ----
    let drag = null;
    let suppressClick = false;   // 拖拽结束后吞掉紧随的 click

    function setChatPos(left, top) {
        const rect = chat.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const maxX = vw - rect.width - MARGIN;
        const maxY = vh - rect.height - MARGIN;
        // 面板展开时，整体占位以面板为准（面板右对齐按钮、在按钮上方）
        const panelOpen = !panel.hidden;
        const minX = panelOpen ? panel.offsetWidth - rect.width + MARGIN : MARGIN;
        const minY = panelOpen ? panel.offsetHeight + 14 + MARGIN : MARGIN;
        left = Math.min(Math.max(left, minX), maxX);
        top = Math.min(Math.max(top, minY), maxY);
        chat.style.left = left + 'px';
        chat.style.top = top + 'px';
        chat.style.right = 'auto';
        chat.style.bottom = 'auto';
    }

    function savePos() {
        const rect = chat.getBoundingClientRect();
        try {
            localStorage.setItem(POS_KEY, JSON.stringify({ left: Math.round(rect.left), top: Math.round(rect.top) }));
        } catch (e) { /* 隐私模式等场景下忽略存储失败 */ }
    }

    // 恢复上次拖拽位置
    (function restorePos() {
        let pos = null;
        try {
            pos = JSON.parse(localStorage.getItem(POS_KEY) || 'null');
        } catch (e) { pos = null; }
        if (pos && typeof pos.left === 'number' && typeof pos.top === 'number') {
            setChatPos(pos.left, pos.top);
        }
    })();

    fab.addEventListener('pointerdown', (e) => {
        drag = {
            pointerId: e.pointerId,
            startX: e.clientX,
            startY: e.clientY,
            baseLeft: chat.getBoundingClientRect().left,
            baseTop: chat.getBoundingClientRect().top,
            moved: false
        };
        fab.setPointerCapture(e.pointerId);
        e.preventDefault();
    });

    fab.addEventListener('pointermove', (e) => {
        if (!drag || e.pointerId !== drag.pointerId) return;
        const dx = e.clientX - drag.startX;
        const dy = e.clientY - drag.startY;
        if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
        drag.moved = true;
        fab.classList.add('is-dragging');
        setChatPos(drag.baseLeft + dx, drag.baseTop + dy);
    });

    function endDrag(e) {
        if (!drag || e.pointerId !== drag.pointerId) return;
        const wasDrag = drag.moved;
        drag = null;
        fab.classList.remove('is-dragging');
        if (wasDrag) savePos();
        return wasDrag;
    }

    fab.addEventListener('pointerup', (e) => {
        if (drag) suppressClick = endDrag(e);
    });
    fab.addEventListener('pointercancel', () => {
        drag = null;
        suppressClick = false;
        fab.classList.remove('is-dragging');
    });

    // 窗口尺寸变化时把挂件收回可视区域
    window.addEventListener('resize', () => {
        const rect = chat.getBoundingClientRect();
        setChatPos(rect.left, rect.top);
    });

    // ---- 面板展开 / 收起 ----
    function openPanel() {
        panel.hidden = false;
        panel.setAttribute('aria-hidden', 'false');
        fab.setAttribute('aria-expanded', 'true');

        // 挂件若被拖到靠左/靠上位置，按面板尺寸校正，确保面板完整显示在视口内
        const cRect = chat.getBoundingClientRect();
        setChatPos(cRect.left, cRect.top);

        inputEl.focus();
        scrollToBottom();
    }

    function closePanel() {
        panel.hidden = true;
        panel.setAttribute('aria-hidden', 'true');
        fab.setAttribute('aria-expanded', 'false');
        fab.focus();
    }

    // pointerup 先于 click 触发：拖拽结束（suppressClick=true）时吞掉本次 click
    fab.addEventListener('click', () => {
        if (suppressClick) {
            suppressClick = false;
            return;
        }
        panel.hidden ? openPanel() : closePanel();
    });

    closeBtn.addEventListener('click', closePanel);
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !panel.hidden) closePanel();
    });

    function scrollToBottom() {
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    // ---- 新回复听觉反馈：轻柔提示音 + 屏幕阅读器播报 ----
    const srStatus = document.getElementById('dm-sr-status');
    const soundToggle = document.getElementById('dm-sound-toggle');
    let audioCtx = null;
    let soundOn = true;
    try { soundOn = localStorage.getItem('dmSound') !== '0'; } catch (e) { /* 隐私模式下忽略存储失败 */ }

    // 首次用户手势时创建/解锁 AudioContext（浏览器自动播放策略要求）
    function ensureAudioCtx() {
        if (!('AudioContext' in window) && !('webkitAudioContext' in window)) return null;
        if (!audioCtx) {
            const AC = window.AudioContext || window.webkitAudioContext;
            try { audioCtx = new AC(); } catch (e) { return null; }
        }
        if (audioCtx.state === 'suspended') audioCtx.resume();
        return audioCtx;
    }
    document.addEventListener('pointerdown', ensureAudioCtx, { once: true });
    document.addEventListener('keydown', ensureAudioCtx, { once: true });

    // 轻柔的双音上行提示（660Hz → 880Hz），音量刻意压低不刺耳，淡入淡出防爆音
    function playReplyChime() {
        if (!soundOn) return;
        const ctx = ensureAudioCtx();
        if (!ctx || ctx.state !== 'running') return;
        const t0 = ctx.currentTime;
        [660, 880].forEach((freq, i) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            const t = t0 + i * 0.13;
            osc.type = 'sine';
            osc.frequency.value = freq;
            gain.gain.setValueAtTime(0, t);
            gain.gain.linearRampToValueAtTime(0.07, t + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.001, t + 0.16);
            osc.connect(gain).connect(ctx.destination);
            osc.start(t);
            osc.stop(t + 0.18);
        });
    }

    // 屏幕阅读器播报：先清空再写入，保证与上一条内容相同时也会被读出
    function announceReply(text) {
        if (!srStatus) return;
        const plain = text.replace(/\*\*/g, '');
        srStatus.textContent = '';
        window.setTimeout(() => {
            srStatus.textContent = '邓妈妈：' + plain;
        }, 60);
    }

    function syncSoundToggle() {
        if (!soundToggle) return;
        soundToggle.textContent = soundOn ? '🔔' : '🔕';
        soundToggle.setAttribute('aria-pressed', String(soundOn));
        soundToggle.setAttribute('aria-label', '新回复提示音：' + (soundOn ? '开启' : '关闭'));
    }
    if (soundToggle) {
        syncSoundToggle();
        soundToggle.addEventListener('click', () => {
            // 有回复正在朗读时，铃铛优先充当“停止朗读”键
            if (msgSpeakBtn && 'speechSynthesis' in window) {
                window.speechSynthesis.cancel();
                resetMsgSpeakUI();
                return;
            }
            soundOn = !soundOn;
            try { localStorage.setItem('dmSound', soundOn ? '1' : '0'); } catch (e) { /* 忽略 */ }
            syncSoundToggle();
        });
    }

    // ---- 单条回复朗读：温柔的女性长辈声音，方便视障用户重听任意一条 ----
    let msgSpeakBtn = null;   // 当前正在朗读的按钮

    function pickGentleVoice() {
        if (!('speechSynthesis' in window)) return null;
        const vs = window.speechSynthesis.getVoices() || [];
        const zh = vs.filter(v => /^zh([-_]|$)/i.test(v.lang));
        // 优先中文女声（微软晓晓/小艺/慧慧等），营造温柔长辈感
        return zh.find(v => /xiaoxiao|xiaoyi|xiaohan|xiaomo|xiaoxuan|huihui|yaoyao|tingting|female|晓/i.test(v.name))
            || zh[0] || vs.find(v => v.default) || null;
    }

    function createSpeakBtn() {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'dm-speak-msg';
        b.setAttribute('aria-label', '朗读这条回复');
        b.setAttribute('aria-pressed', 'false');
        b.textContent = '🔊 朗读';
        return b;
    }

    function resetMsgSpeakUI() {
        if (!msgSpeakBtn) return;
        msgSpeakBtn.classList.remove('speaking');
        msgSpeakBtn.setAttribute('aria-pressed', 'false');
        msgSpeakBtn.textContent = '🔊 朗读';
        msgSpeakBtn = null;
        syncSoundToggle();   // 恢复铃铛的提示音开关语义
    }

    // 事件委托：欢迎语、历史回复、后续新回复的朗读按钮统一处理
    messagesEl.addEventListener('click', (e) => {
        const btn = e.target.closest('.dm-speak-msg');
        if (!btn || !('speechSynthesis' in window)) return;
        const synth = window.speechSynthesis;
        if (msgSpeakBtn === btn) {   // 再点同一按钮 = 停止
            synth.cancel();
            resetMsgSpeakUI();
            return;
        }
        const bubble = btn.closest('.dm-bubble');
        if (!bubble) return;
        // 取气泡纯文本（剔除朗读按钮自身文字）
        const clone = bubble.cloneNode(true);
        clone.querySelectorAll('.dm-speak-msg').forEach(b => b.remove());
        const text = clone.textContent
            .replace(/(\d)\s*[-–—]\s*(\d)/g, '$1，$2')   // 号码连字符读作停顿
            .replace(/\d{3,}/g, m => m.split('').join(' '))  // 长数字串逐位朗读（热线号码）
            .trim();
        if (!text) return;
        synth.cancel();
        resetMsgSpeakUI();
        const utt = new SpeechSynthesisUtterance(text);
        utt.lang = 'zh-CN';
        const voice = pickGentleVoice();
        if (voice) utt.voice = voice;
        utt.rate = 0.9;    // 语速稍慢，像长辈慢慢讲
        utt.pitch = 1.05;  // 音调略柔
        utt.onend = resetMsgSpeakUI;
        utt.onerror = resetMsgSpeakUI;
        msgSpeakBtn = btn;
        btn.classList.add('speaking');
        btn.setAttribute('aria-pressed', 'true');
        btn.textContent = '⏸ 停止';
        if (soundToggle) soundToggle.setAttribute('aria-label', '停止朗读');  // 朗读中铃铛变为停止键
        synth.speak(utt);
    });

    // ---- 邓妈妈回复渲染：先转义 HTML 防 XSS，再把 **文字** 渲染为 <strong>文字</strong> ----
    function renderBotHtml(text) {
        const escaped = text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
        return escaped
            .replace(/\*\*([\s\S]+?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*\*/g, '');   // 去掉未配对的残留 **
    }

    // ---- 创建一行消息（role: 'bot' 邓妈妈靠左 / 'user' 用户靠右）----
    function appendRow(role, text) {
        const row = document.createElement('div');
        row.className = 'dm-row ' + (role === 'user' ? 'dm-row-user' : 'dm-row-bot');

        if (role !== 'user') {
            const avatar = document.createElement('img');
            avatar.className = 'dm-avatar';
            avatar.src = 'dengmama.jpg?v=20261002g';
            avatar.alt = '';
            row.appendChild(avatar);
        }

        const bubble = document.createElement('div');
        bubble.className = 'dm-bubble ' + (role === 'user' ? 'dm-bubble-user' : 'dm-bubble-bot');
        if (role === 'user') {
            bubble.textContent = text;   // 用户输入保持纯文本，避免 XSS
        } else {
            bubble.innerHTML = renderBotHtml(text);
            bubble.appendChild(createSpeakBtn());   // 每条回复附朗读按钮
        }
        row.appendChild(bubble);

        messagesEl.appendChild(row);
        scrollToBottom();
        return bubble;
    }

    // ---- “邓妈妈正在思考…”占位气泡 ----
    function appendThinking() {
        const row = document.createElement('div');
        row.className = 'dm-row dm-row-bot';

        const avatar = document.createElement('img');
        avatar.className = 'dm-avatar';
        avatar.src = 'dengmama.jpg?v=20261002g';
        avatar.alt = '';
        row.appendChild(avatar);

        const bubble = document.createElement('div');
        bubble.className = 'dm-bubble dm-bubble-bot dm-bubble-thinking';

        const text = document.createElement('span');
        text.textContent = '邓妈妈正在思考…';
        bubble.appendChild(text);

        const dots = document.createElement('span');
        dots.className = 'dm-typing';
        dots.innerHTML = '<i></i><i></i><i></i>';
        bubble.appendChild(dots);

        row.appendChild(bubble);
        messagesEl.appendChild(row);
        scrollToBottom();
        return bubble;
    }

    // 用最终回复替换占位气泡
    function replaceThinking(bubble, reply) {
        bubble.classList.remove('dm-bubble-thinking');
        bubble.innerHTML = renderBotHtml(reply);
        bubble.appendChild(createSpeakBtn());   // 回复附朗读按钮
        scrollToBottom();
        playReplyChime();   // 轻柔提示音：不止依赖视觉弹出
        announceReply(reply);   // 同步到 aria-live 播报区，屏幕阅读器自动读出
    }

    function setBusy(state) {
        busy = state;
        sendBtn.disabled = state;
        inputEl.disabled = state;
        presetsEl.querySelectorAll('.dm-preset').forEach(btn => {
            btn.disabled = state;
        });
    }

    // ---- 发送消息并等待后端回复 ----
    async function sendMessage(rawText) {
        const text = (rawText || '').trim();
        if (!text || busy) return;

        setBusy(true);
        inputEl.value = '';
        appendRow('user', text);
        const thinkingBubble = appendThinking();

        try {
            const resp = await fetch(API_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text })
            });

            if (!resp.ok) {
                throw new Error('后端返回异常状态码：' + resp.status);
            }

            const data = await resp.json();
            const reply = data && typeof data.reply === 'string' ? data.reply.trim() : '';
            if (!reply) {
                throw new Error('返回内容中缺少 reply 字段');
            }

            replaceThinking(thinkingBubble, reply);
        } catch (err) {
            console.error('[邓妈妈] 对话请求失败：', err);
            replaceThinking(thinkingBubble, '孩子，我这会儿暂时连不上服务，你可以稍后再试；若情况紧急，请直接拨打 12338 妇女维权热线。');
        } finally {
            setBusy(false);
            inputEl.focus();
        }
    }

    // ---- 输入区交互：点击发送 / 回车发送 ----
    sendBtn.addEventListener('click', () => sendMessage(inputEl.value));
    inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            sendMessage(inputEl.value);
        }
    });

    // ---- 8 个预置问题：点击后自动发送对应提问 ----
    presetsEl.addEventListener('click', (e) => {
        const btn = e.target.closest('.dm-preset');
        if (!btn || busy) return;
        sendMessage(btn.dataset.question);
    });
})();

/* ===== 无障碍工具栏：字体缩放 / 朗读本页 / 高对比度 / 灰度 ===== */
(function () {
    const root = document.documentElement;
    const siteContent = document.getElementById('site-content');
    const dmChat = document.getElementById('dm-chat');
    const bar = document.getElementById('a11y-bar');
    if (!siteContent || !bar) return;

    function store(key, val) {
        try { localStorage.setItem(key, val); } catch (e) { /* 隐私模式下忽略 */ }
    }
    function read(key) {
        try { return localStorage.getItem(key); } catch (e) { return null; }
    }

    /* ---- 1. 字体大小调节：zoom 等比缩放正文与挂件（px 布局也随动），90%~150% ---- */
    const FONT_STEPS = [90, 100, 110, 125, 150];
    const FONT_KEY = 'za-font';
    const fontDec = document.getElementById('font-dec');
    const fontInc = document.getElementById('font-inc');
    const fontVal = document.getElementById('font-val');
    let fontIdx = FONT_STEPS.indexOf(100);

    function applyFont() {
        const pct = FONT_STEPS[fontIdx];
        const zoom = pct + '%';
        siteContent.style.zoom = zoom;
        if (dmChat) dmChat.style.zoom = zoom;
        if (fontVal) fontVal.textContent = pct + '%';
        store(FONT_KEY, String(pct));
    }

    (function initFont() {
        const saved = parseInt(read(FONT_KEY), 10);
        const idx = FONT_STEPS.indexOf(saved);
        if (idx >= 0) fontIdx = idx;
        applyFont();
    })();

    if (fontDec) fontDec.addEventListener('click', () => {
        if (fontIdx > 0) { fontIdx--; applyFont(); }
    });
    if (fontInc) fontInc.addEventListener('click', () => {
        if (fontIdx < FONT_STEPS.length - 1) { fontIdx++; applyFont(); }
    });

    /* ---- 2. 朗读本页：speechSynthesis，限定正文边界，排除按钮/导航/弹窗 ---- */
    const speakBtn = document.getElementById('speak-page');
    const speakText = speakBtn ? speakBtn.querySelector('.a11y-speak-text') : null;
    const synth = ('speechSynthesis' in window && 'SpeechSynthesisUtterance' in window) ? window.speechSynthesis : null;
    let speaking = false;
    let voices = [];

    function refreshVoices() {
        if (synth) voices = synth.getVoices() || [];
    }
    if (synth) {
        refreshVoices();
        // 部分浏览器语音列表异步加载
        synth.addEventListener('voiceschanged', refreshVoices);
    }

    function pickVoice() {
        return voices.find(v => v.lang === 'zh-CN')
            || voices.find(v => /^zh([-_]|$)/i.test(v.lang))
            || voices.find(v => v.default)
            || null;
    }

    function setSpeakUI(on) {
        speaking = on;
        speakBtn.classList.toggle('speaking', on);
        speakBtn.setAttribute('aria-pressed', String(on));
        if (speakText) speakText.textContent = on ? '停止朗读' : '朗读此页';
        speakBtn.setAttribute('aria-label', on ? '停止朗读' : '朗读本页核心内容，再次点击停止');
    }

    // 提取页面核心正文：仅热线/简介/场景指南/暖心疗愈四区，剔除交互控件与隐藏内容
    // 热线名称/号码/简介必须包含——这些是视障用户最需要听到的信息
    function collectSpeechText() {
        const holder = document.createElement('div');
        ['.sos-zone', '.hero', '#prevention', '#recovery'].forEach(sel => {
            const sec = document.querySelector(sel);
            if (sec) holder.appendChild(sec.cloneNode(true));
        });
        // 剔除链接但保留热线号码（a.sos-number 是关键信息）
        holder.querySelectorAll('button, a:not(.sos-number), nav, script, style, .modal, [hidden], [aria-hidden="true"]').forEach(el => el.remove());
        const blocks = [];
        holder.querySelectorAll('h1, h2, h3, h4, p, li, summary, .sos-badge, .sos-number, .sos-tag, .hero-badge, .channel-name, .channel-num, .channel-desc').forEach(el => {
            // 未展开的 details 正文不读（summary 标题保留）
            const det = el.closest('details');
            if (det && !det.open && el.tagName.toLowerCase() !== 'summary') return;
            const text = el.textContent
                .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}\u{200D}]/gu, '')  // 过滤 emoji
                .replace(/(\d)\s*[-–—]\s*(\d)/g, '$1，$2')   // 号码中的连字符读作停顿
                .replace(/\d{3,}/g, m => m.split('').join(' '))  // 3位以上数字串逐位朗读（热线号码）
                .replace(/\s+/g, ' ')
                .trim();
            if (text) blocks.push(text);
        });
        return blocks;
    }

    function stopSpeaking() {
        if (synth) synth.cancel();
        setSpeakUI(false);
    }

    function startSpeaking() {
        const blocks = collectSpeechText();
        if (!blocks.length) return;
        synth.cancel();   // 清空可能残留的朗读队列
        const voice = pickVoice();
        blocks.forEach((text, i) => {
            const utt = new SpeechSynthesisUtterance(text);
            utt.lang = 'zh-CN';
            if (voice) utt.voice = voice;
            utt.rate = 1;
            if (i === 0) utt.onstart = () => setSpeakUI(true);
            if (i === blocks.length - 1) utt.onend = () => setSpeakUI(false);
            utt.onerror = (e) => {
                // 用户主动停止触发 interrupted/canceled，属正常流程不算错误
                if (e.error === 'interrupted' || e.error === 'canceled') return;
                console.error('[无障碍] 朗读失败：', e.error);
                setSpeakUI(false);
            };
            synth.speak(utt);
        });
    }

    if (speakBtn) {
        if (!synth) {
            // 浏览器不支持语音合成：禁用并明确告知，不静默失败
            speakBtn.disabled = true;
            speakBtn.setAttribute('aria-label', '当前浏览器不支持语音朗读功能');
            if (speakText) speakText.textContent = '不支持朗读';
        } else {
            speakBtn.addEventListener('click', () => {
                speaking ? stopSpeaking() : startSpeaking();
            });
            // 页面离开时停止朗读，避免后台继续发声
            window.addEventListener('pagehide', () => synth.cancel());
        }
    }

    /* ---- 3. 高对比度 / 灰度模式：互斥切换，记忆偏好 ---- */
    const contrastBtn = document.getElementById('contrast-toggle');
    const grayBtn = document.getElementById('grayscale-toggle');

    function applyMode(btn, cls, on, key) {
        root.classList.toggle(cls, on);
        if (btn) btn.setAttribute('aria-pressed', String(on));
        store(key, on ? '1' : '0');
    }

    function setContrast(on) {
        applyMode(contrastBtn, 'high-contrast', on, 'za-hc');
        if (on && grayBtn) applyMode(grayBtn, 'grayscale', false, 'za-gray');
    }
    function setGray(on) {
        applyMode(grayBtn, 'grayscale', on, 'za-gray');
        if (on && contrastBtn) applyMode(contrastBtn, 'high-contrast', false, 'za-hc');
    }

    // 初始化：以 <html> 上已有的 class 为准（head 内联脚本已提前恢复，防闪烁）
    if (contrastBtn) {
        contrastBtn.setAttribute('aria-pressed', String(root.classList.contains('high-contrast')));
        contrastBtn.addEventListener('click', () => setContrast(!root.classList.contains('high-contrast')));
    }
    if (grayBtn) {
        grayBtn.setAttribute('aria-pressed', String(root.classList.contains('grayscale')));
        grayBtn.addEventListener('click', () => setGray(!root.classList.contains('grayscale')));
    }
})();

// =====================================================
// PWA · 安装到主屏幕
// Chrome/Edge 会捕获 beforeinstallprompt；Safari 不支持该事件，
// 因此按钮在 Safari 上改为提示「分享 → 添加到主屏幕」。
// =====================================================
(function () {
    const btn = document.getElementById('pwa-install-btn');
    if (!btn) return;

    const hint = document.getElementById('pwa-install-hint');
    let deferred = null;

    window.addEventListener('beforeinstallprompt', (e) => {
        e.preventDefault();
        deferred = e;
        btn.hidden = false;
    });

    btn.addEventListener('click', async () => {
        if (deferred) {
            btn.hidden = true;
            deferred.prompt();
            try { await deferred.userChoice; } catch (err) { /* 忽略 */ }
            deferred = null;
            return;
        }
        // Safari / 不支持安装的场景
        if (hint) {
            hint.hidden = false;
            setTimeout(() => { hint.hidden = true; }, 6000);
        }
    });

    window.addEventListener('appinstalled', () => {
        btn.hidden = true;
        if (hint) hint.textContent = '已添加到主屏幕 ✓';
    });
})();