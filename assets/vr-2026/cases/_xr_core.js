/* ============================================================
   XR 核心工程模块 v2（六个 WebXR 课堂案例共用）
   ------------------------------------------------------------
   提供"VR 作为一门工程学科"必须具备的六件事：
   ① 性能：帧预算（PICO Neo3 = 72Hz / 13.9ms）、实时统计、动态降级
   ② 人因：隧道视野（晕动缓解）、移动/转向范式、眼高校准、休息提示
   ③ 数据：体验过程记录（时长/帧率分布/晕动暴露/交互数）→ 导出 JSON
   ④ 界面：VR 内可点的世界空间工程面板 + PC 端 DOM 面板
   ⑤ 可访问性：坐姿/站立、惯用手、单手模式、注视停留（免手柄）点击
   ⑥ 目标闭环：统一成就/结果记录 XRCore.goal()，六例共用一套口径

   接入方式（每例 3 处）：
     <script src="../_xr_core.js"></script>
     XRCore.init({THREE:THREE,renderer:renderer,scene:scene,camera:camera,player:player,
                  name:'案例名', panelPos:[x,y,z], panelRotY:rad, home:{x,z,rotY},
                  bounds:{x:[..],z:[..]}, tips:[...], onPose:function(pose){}});
     // animate() 内：XRCore.frame(dt);   // VR 移动：XRCore.vrMove(dt) 取代各例自有的 updVRMove
   ============================================================ */
(function(){
'use strict';

var T=null, renderer=null, scene=null, camera=null, player=null;
var S={
  name:'', targetFps:72, budgetMs:13.9,
  fps:0, ms:0, msAvg:0, msP99:0, calls:0, tris:0, tex:0,
  moveMode:'teleport',   /* 'teleport'(瞬移/短闪) | 'smooth'(平滑) */
  turnMode:'snap',       /* 'snap'(45°) | 'smooth' */
  vignette:0.80,         /* 隧道视野强度 0..1，0=关闭 */
  speed:1.5, dashDist:1.35, snapDeg:45,
  sessionMs:0, smoothMs:0, teleports:0, interactions:0,
  degrade:0, eyeH:0, rested:false, restAt:600000,
  vigTarget:0, vig:0, snapCd:0, dashCd:0,
  /* --- v2 可访问性 --- */
  pose:'standing',       /* 'standing' | 'seated' */
  hand:'auto',           /* 'auto' | 'right' | 'left'（主手，单手模式用） */
  oneHand:false,         /* 单手模式：只接受主手输入，另一手完全忽略 */
  gaze:false,            /* 注视停留点击（免手柄/肢体不便时的替代输入通道） */
  gazeMs:1200,           /* 停留多久触发 */
  /* --- v2 目标闭环 --- */
  goals:[],
  tips:[], tipIdx:0, tipT:0
};
var msHist=[], acc=0, accN=0, statT=0, overT=0, underT=0;
var lastPos=null, tmpV=null, tmpV2=null, fwdV=null, sideV=null;
var vigMesh=null, vigMat=null;
var panel=null, panelCnv=null, panelTex=null, panelBtns=[];
var panelBase={x:0,y:0,z:0,rx:0}, PORTRAIT_H=460, PORTRAIT_W=512;
var home=null, bounds=null, ownLocomotion=false, ownHook=null, ownText=null, poseHook=null, inited=false;
var ray=null, listenersBound=false;
/* gaze 专用向量与状态 */
var gRay=null, gFrom=null, gDir=null, gazeId=null, gazeT=0, gazeFired=null, gazeP=0, gazeRedraw=0;
/* 注视准星（挂在相机上，命中 UI 时收缩+变金，给出"马上要触发"的反馈） */
var ret=null, retRing=null, retDot=null, retDist=0;
/* 案例自有 UI 的命中委托：XRCore.uiHit(fn)，fn(raycaster) -> {id, fire, dist} | null */
var uiHitFn=null, uiHitWarned=false;

/* ---------- 工具 ---------- */
function clamp(v,a,b){ return v<a?a:(v>b?b:v); }
function lerp(a,b,t){ return a+(b-a)*t; }
function cnv(w,h){ var c=document.createElement('canvas'); c.width=w; c.height=h;
  return {c:c, x:c.getContext('2d'), w:w, h:h}; }
function wrap(x, text, px, py, maxW, lh, maxLine){
  var line='', out=[], i, ch;
  for(i=0;i<text.length;i++){
    ch=text.charAt(i);
    if(x.measureText(line+ch).width>maxW){ out.push(line); line=ch; }
    else line+=ch;
  }
  if(line) out.push(line);
  for(i=0;i<out.length && i<maxLine;i++) x.fillText(out[i], px, py+i*lh);
}
function curTarget(){
  return (S.moveMode==='teleport'? '瞬移(短闪)' : '平滑') + ' / ' + (S.turnMode==='snap'? ('Snap '+S.snapDeg+'°') : '平滑');
}
function handText(){ return S.oneHand? (S.hand==='auto'?'单手·自适应':(S.hand==='right'?'单手·右手':'单手·左手')) : '双手'; }
function poseText(){ return S.pose==='seated'? '坐姿' : '站立'; }

/* ---------- 隧道视野遮罩（挂在相机上，VR 内跟随视线） ---------- */
function buildVignette(){
  var o=cnv(256,256), x=o.x;
  var g=x.createRadialGradient(128,128,54,128,128,132);
  g.addColorStop(0,'rgba(0,0,0,0)');
  g.addColorStop(0.62,'rgba(0,0,0,0.55)');
  g.addColorStop(1,'rgba(0,0,0,1)');
  x.fillStyle=g; x.fillRect(0,0,256,256);
  var vigTex=new T.CanvasTexture(o.c); vigTex.needsUpdate=true;
  vigMat=new T.MeshBasicMaterial({map:vigTex, transparent:true, opacity:0, depthTest:false, depthWrite:false});
  vigMesh=new T.Mesh(new T.PlaneGeometry(1.9,1.9), vigMat);
  vigMesh.position.set(0,0,-0.42);
  vigMesh.renderOrder=9999;
  vigMesh.visible=false;
  camera.add(vigMesh);
}

/* ---------- 工程面板（世界空间，VR 可点 / 可注视） ---------- */
function buildPanel(pos, rotY){
  panelCnv=cnv(PORTRAIT_W,PORTRAIT_H); panelTex=new T.CanvasTexture(panelCnv.c);
  panel=new T.Mesh(new T.PlaneGeometry(0.54, 0.54*PORTRAIT_H/PORTRAIT_W),
    new T.MeshBasicMaterial({map:panelTex, transparent:true, depthWrite:false}));
  panel.position.set(pos[0],pos[1],pos[2]);
  panel.rotation.y=rotY||0;
  scene.add(panel);
  panelBase.x=pos[0]; panelBase.y=pos[1]; panelBase.z=pos[2]; panelBase.rx=panel.rotation.x;
  panelBtns=[
    {id:'move',  x:16,  y:320, w:150, h:38},
    {id:'turn',  x:174, y:320, w:150, h:38},
    {id:'vig',   x:332, y:320, w:164, h:38},
    {id:'pose',  x:16,  y:366, w:150, h:38},
    {id:'hand',  x:174, y:366, w:150, h:38},
    {id:'gaze',  x:332, y:366, w:164, h:38},
    {id:'reset', x:16,  y:410, w:240, h:36},
    {id:'dump',  x:264, y:410, w:232, h:36}
  ];
  drawPanel();
}
function drawPanel(){
  if(!panelCnv) return;
  var x=panelCnv.x, W=PORTRAIT_W, H=PORTRAIT_H, i;
  x.clearRect(0,0,W,H);
  var g=x.createLinearGradient(0,0,0,H);
  g.addColorStop(0,'rgba(10,14,20,0.94)'); g.addColorStop(1,'rgba(18,24,32,0.92)');
  x.fillStyle=g; x.fillRect(0,0,W,H);
  x.strokeStyle='rgba(127,224,255,0.5)'; x.lineWidth=3; x.strokeRect(2,2,W-4,H-4);
  x.textAlign='left'; x.textBaseline='alphabetic';
  x.fillStyle='#7fe0ff'; x.font='bold 24px "Microsoft YaHei",sans-serif';
  x.fillText('XR 工程面板', 16, 30);
  x.fillStyle='#9fb0c6'; x.font='17px "Microsoft YaHei",sans-serif';
  x.fillText(S.name, 140, 30);

  /* 性能 */
  var col=(S.msAvg<=S.budgetMs)?'#8fe08a':((S.msAvg<=S.budgetMs*1.25)?'#ffd873':'#ff7a6a');
  x.fillStyle=col; x.font='bold 26px "Microsoft YaHei",sans-serif';
  x.fillText(S.fps+' FPS', 16, 66);
  x.fillStyle='#cfd8e4'; x.font='17px "Microsoft YaHei",sans-serif';
  x.fillText('帧 '+S.msAvg.toFixed(1)+'ms / 最差1% '+S.msP99.toFixed(1)+'ms · 预算 '+S.budgetMs+'ms(72Hz)', 110, 66);
  x.fillText('DrawCalls '+S.calls+' · 三角面 '+S.tris+' · 贴图 '+S.tex+' · 降级 L'+S.degrade, 16, 92);

  /* 人因 */
  x.fillStyle='#e8eef6'; x.font='17px "Microsoft YaHei",sans-serif';
  x.fillText('移动/转向：'+(ownLocomotion? (ownText? ownText() : '本例自带') : curTarget()), 16, 122);
  x.fillText('隧道视野：'+(S.vignette<=0.01? '关' : (S.vignette*100|0)+'%')+'（平滑移动时自动收窄）', 16, 146);
  x.fillText('眼高 '+S.eyeH.toFixed(2)+' m · 已体验 '+(S.sessionMs/1000/60).toFixed(1)+' 分钟'+
             (S.rested? ' · ⚠建议休息' : ''), 16, 170);

  /* 数据 */
  x.fillStyle='#9fb0c6';
  x.fillText('晕动暴露 '+(S.smoothMs/1000).toFixed(1)+'s · 瞬移 '+S.teleports+' 次 · 交互 '+S.interactions+' 次', 16, 194);

  /* 可访问性 + 目标闭环 */
  x.fillStyle=S.oneHand||S.gaze||S.pose==='seated'? '#ffd873' : '#9fb0c6';
  x.fillText('可访问性：'+poseText()+' · '+handText()+' · 注视点击'+(S.gaze?'开':'关')+
             ' · 目标 '+S.goals.length+' 项', 16, 218);

  /* 学科要点（每例注入，9 秒轮播） */
  if(S.tips.length){
    var tip=S.tips[S.tipIdx%S.tips.length];
    x.fillStyle='rgba(127,224,255,0.10)'; x.fillRect(12,238,W-24,72);
    x.fillStyle='#7fe0ff'; x.font='bold 17px "Microsoft YaHei",sans-serif';
    x.fillText('要点 '+(S.tipIdx%S.tips.length+1)+'/'+S.tips.length+' · '+tip[0], 18, 258);
    x.fillStyle='#d8e2ee'; x.font='16px "Microsoft YaHei",sans-serif';
    wrap(x, tip[1], 18, 276, W-36, 21, 2);
  }

  /* 按钮 */
  for(i=0;i<panelBtns.length;i++){
    var b=panelBtns[i];
    x.fillStyle='rgba(46,93,143,0.85)'; x.fillRect(b.x,b.y,b.w,b.h);
    /* 注视停留进度：让用户看得见"再盯一会就会触发"，避免误操作 */
    if(S.gaze && gazeId===b.id && gazeP>0.01){
      x.fillStyle='rgba(255,216,115,0.62)';
      x.fillRect(b.x, b.y+b.h-6, Math.min(b.w, b.w*gazeP), 6);
    }
    x.strokeStyle='rgba(255,255,255,0.22)'; x.lineWidth=2; x.strokeRect(b.x,b.y,b.w,b.h);
    x.fillStyle='#f2f6fb'; x.font='bold 18px "Microsoft YaHei",sans-serif';
    x.textAlign='center'; x.textBaseline='middle';
    var lb={move:'移动: '+(S.moveMode==='teleport'?'瞬移':'平滑'),
            turn:'转向: '+(S.turnMode==='snap'?'Snap':'平滑'),
            vig:'隧道 '+((S.vignette*100|0)+'%'),
            pose:'姿态: '+poseText(),
            hand:'主手: '+(S.oneHand? (S.hand==='auto'?'自适应':(S.hand==='right'?'右手':'左手')) : '双手'),
            gaze:'注视点击: '+(S.gaze?'开':'关'),
            reset:'重置视角 (Y)', dump:'导出体验数据'}[b.id];
    x.fillText(lb, b.x+b.w/2, b.y+b.h/2+1);
    x.textAlign='left'; x.textBaseline='alphabetic';
  }
  panelTex.needsUpdate=true;
}

/* ---------- 姿态切换：坐姿时可交互面板整体下沉并轻微下倾（可及性） ---------- */
var uiNodes=[];
function registerUI(list){
  for(var i=0;i<list.length;i++){
    var m=list[i]; if(!m) continue;
    /* 去重：同一 mesh 重复注册不叠加 */
    for(var j=0;j<uiNodes.length;j++) if(uiNodes[j].m===m) { m=null; break; }
    if(m) uiNodes.push({m:m, y:m.position.y, rx:m.rotation.x});
  }
  applyPose();
}
function applyPose(){
  var seated=(S.pose==='seated'), drop=seated?0.45:0, tilt=seated?-0.18:0, i;
  if(panel){
    panel.position.y=panelBase.y-drop;
    panel.rotation.x=panelBase.rx+tilt;
  }
  for(i=0;i<uiNodes.length;i++){
    var n=uiNodes[i];
    n.m.position.y=n.y-drop;
    n.m.rotation.x=n.rx+tilt;
  }
  if(typeof poseHook==='function'){ try{ poseHook(S.pose); }catch(e){} }
}

/* ---------- PC DOM 面板 ---------- */
var domPanel=null, domStat=null;
function buildDom(){
  var st=document.createElement('style');
  st.textContent='#xrcfg{position:fixed;right:12px;top:64px;z-index:16;width:250px;'+
    'background:rgba(10,14,20,.94);border:1px solid #3a4658;border-radius:10px;padding:9px 11px;'+
    'color:#cfd8e4;font-size:12px;}.XR #xrcfg{display:none;}'+
    '#xrcfg h4{margin:0 0 6px;color:#7fe0ff;font-size:12px;}'+
    '#xrcfg button{width:100%;margin:3px 0;padding:5px 0;border:0;border-radius:6px;'+
    'background:#2e5d8f;color:#fff;font-size:12px;cursor:pointer;}'+
    '#xrcfg .st{line-height:1.7;color:#9fb0c6;}';
  document.head.appendChild(st);
  domPanel=document.createElement('div'); domPanel.id='xrcfg';
  domPanel.innerHTML='<h4>XR 工程面板（PC）</h4><div class="st"></div>';
  var ids=[['move','移动范式：瞬移/平滑'],['turn','转向范式：Snap/平滑'],
           ['vig','隧道视野强度'],['pose','坐姿 / 站立'],['hand','惯用手 / 单手'],
           ['gaze','注视停留点击'],['reset','重置视角'],['dump','导出体验数据 JSON']];
  ids.forEach(function(p){
    var b=document.createElement('button'); b.textContent=p[1];
    b.addEventListener('click', function(){ fire(p[0]); });
    domPanel.appendChild(b);
  });
  document.body.appendChild(domPanel);
  domStat=domPanel.querySelector('.st');
}
function updDom(){
  if(!domStat) return;
  domStat.innerHTML=
    'FPS <b>'+S.fps+'</b> · 帧 <b>'+S.msAvg.toFixed(1)+'</b>ms（预算 '+S.budgetMs+'）<br>'+
    'DrawCalls '+S.calls+' · 三角 '+S.tris+' · 降级 L'+S.degrade+'<br>'+
    '移动/转向：'+curTarget()+'<br>隧道 '+(S.vignette*100|0)+'% · 眼高 '+S.eyeH.toFixed(2)+'m<br>'+
    '姿态 '+poseText()+' · '+handText()+' · 注视'+(S.gaze?'开':'关')+' · 目标 '+S.goals.length+'<br>'+
    '时长 '+(S.sessionMs/1000|0)+'s · 晕动暴露 '+(S.smoothMs/1000).toFixed(1)+'s · 交互 '+S.interactions+
    (S.rested? '<br><span style="color:#ff9a3c">⚠ 已连续体验 10 分钟，建议摘下头显休息</span>' : '');
}

/* ---------- 目标闭环（六例共用一套成就口径） ---------- */
function goal(id, name, detail){
  var g={ id:id, name:name||id, t:Math.round(S.sessionMs),
          detail:detail||{}, ts:new Date().toISOString() };
  for(var i=0;i<S.goals.length;i++){
    if(S.goals[i].id===id){ S.goals[i]=g; drawPanel(); updDom(); return g; }
  }
  S.goals.push(g);
  drawPanel(); updDom();
  return g;
}

/* ---------- 动作 ---------- */
function fire(id){
  S.interactions++;
  /* 案例自带移动/转向范式时（01/02），按钮委托给案例自己的开关，避免两套设置源 */
  if(ownLocomotion && (id==='move'||id==='turn')){
    if(ownHook){ ownHook(id); drawPanel(); updDom(); return; }
    drawPanel(); updDom(); return;
  }
  if(id==='move'){ S.moveMode=(S.moveMode==='teleport')?'smooth':'teleport'; }
  else if(id==='turn'){ S.turnMode=(S.turnMode==='snap')?'smooth':'snap'; }
  else if(id==='vig'){ S.vignette=(S.vignette>=0.85)?0:((S.vignette+0.25)||0.25); if(S.vignette>0.85) S.vignette=0.85; }
  else if(id==='pose'){ S.pose=(S.pose==='standing')?'seated':'standing'; applyPose(); }
  else if(id==='hand'){
    /* 循环：双手 → 单手自适应 → 单手右手 → 单手左手 → 双手 */
    if(!S.oneHand){ S.oneHand=true; S.hand='auto'; }
    else if(S.hand==='auto'){ S.hand='right'; }
    else if(S.hand==='right'){ S.hand='left'; }
    else { S.oneHand=false; S.hand='auto'; }
  }
  else if(id==='gaze'){ S.gaze=!S.gaze; gazeId=null; gazeT=0; gazeFired=null; gazeP=0; }
  else if(id==='reset'){ recenter(); }
  else if(id==='dump'){ dump(true); }
  drawPanel(); updDom();
}
function recenter(){
  if(player && home){
    player.position.set(home.x||0, 0, home.z||0);
    player.rotation.y=(home.rotY||0);
  } else if(player){ player.rotation.y=0; }
  if(camera){ camera.rotation.x=0; camera.rotation.z=0; }
  S.vig=0; S.vigTarget=0;
}
/* save=true 才真正下载文件（UI 按钮走这条）；自动化测试只取数据，避免无头浏览器弹下载框卡死 */
function dump(save){
  var d=stats();
  var auto=false;
  try{ auto=!!(navigator && navigator.webdriver); }catch(e){}
  if(save && !auto){
    try{
      var s=JSON.stringify(d,null,1);
      var blob=new Blob([s],{type:'application/json'});
      var a=document.createElement('a');
      a.href=URL.createObjectURL(blob);
      a.download=(S.name||'xr')+'_体验数据.json';
      document.body.appendChild(a); a.click();
      setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); }, 600);
    }catch(e){ /* 下载失败（如 VR 内）不影响返回值 */ }
  }
  window.__xrDump=d;
  return d;
}
function stats(){
  return {
    case:S.name, targetFps:S.targetFps, budgetMs:S.budgetMs,
    sessionMs:Math.round(S.sessionMs),
    fps:S.fps, frameMsAvg:+S.msAvg.toFixed(2), frameMsP99:+S.msP99.toFixed(2),
    drawCalls:S.calls, triangles:S.tris, textures:S.tex,
    degradeLevel:S.degrade,
    comfort:{moveMode:S.moveMode, turnMode:S.turnMode, vignette:S.vignette},
    accessibility:{pose:S.pose, handed:S.hand, oneHand:S.oneHand, gazeDwell:S.gaze, gazeMs:S.gazeMs},
    cybersicknessExposureMs:Math.round(S.smoothMs),
    teleports:S.teleports, interactions:S.interactions,
    eyeHeight:+S.eyeH.toFixed(3), rested:S.rested,
    goals:S.goals.slice(),
    ts:new Date().toISOString()
  };
}

/* ---------- 动态降级 ---------- */
function applyDegrade(){
  if(!renderer) return;
  var want=1.0;
  if(S.degrade>=1) want=0.78;
  if(S.degrade>=2) want=0.62;
  var pr=Math.min(window.devicePixelRatio||1,2)*want;
  if(Math.abs(pr-renderer.getPixelRatio())>0.02){
    try{ renderer.setPixelRatio(pr); }catch(e){}
  }
  var wantShadow=(S.degrade<2);
  if(renderer.shadowMap && renderer.shadowMap.enabled!==wantShadow){
    renderer.shadowMap.enabled=wantShadow;
    try{
      scene.traverse(function(o){
        if(o.material){
          var ms=Array.isArray(o.material)?o.material:[o.material];
          for(var i=0;i<ms.length;i++) if(ms[i]) ms[i].needsUpdate=true;
        }
      });
    }catch(e){}
  }
}

/* ---------- 注视准星（reticle）：注视停留的视觉反馈 ---------- */
function buildRet(){
  if(ret||!camera||!T) return;
  ret=new T.Group();
  retRing=new T.Mesh(new T.RingGeometry(0.013,0.019,28),
    new T.MeshBasicMaterial({color:0xffffff, transparent:true, opacity:0.5, depthTest:false, side:T.DoubleSide}));
  retDot=new T.Mesh(new T.CircleGeometry(0.0035,16),
    new T.MeshBasicMaterial({color:0xffffff, transparent:true, opacity:0.9, depthTest:false}));
  /* 关掉射线参与：准星绝不能被任何案例的 raycaster 命中 */
  retRing.raycast=function(){}; retDot.raycast=function(){};
  retRing.renderOrder=9999; retDot.renderOrder=9999;
  ret.add(retRing); ret.add(retDot);
  ret.position.set(0,0,-1.0);
  ret.visible=false;
  camera.add(ret);
}
function retTick(){
  if(!ret) buildRet();
  if(!ret) return;
  if(!S.gaze){ ret.visible=false; return; }
  ret.visible=true;
  var hit=(gazeId!==null), p=hit?gazeP:0;
  /* 收缩 + 变金 = 进度反馈；不用重建几何，零 GC 压力 */
  retRing.scale.setScalar(1-0.5*p);
  retRing.material.color.setHex(p>0.02?0xffd873:0xffffff);
  retRing.material.opacity=hit?0.92:0.40;
  retDot.material.opacity=hit?0.92:0.35;
  ret.position.z=-(retDist>0.3? retDist : 1.0);
}

/* ---------- 注视停留（gaze dwell）：无需手柄的输入通道 ---------- */
/* 内核面板命中，同时返回命中距离（给准星定位用） */
function coreProbe(rc, wantDist){
  if(!panel) return null;
  var h=rc.intersectObject(panel,false);
  if(!h.length||!h[0].uv) return null;
  var px=h[0].uv.x*PORTRAIT_W, py=(1-h[0].uv.y)*PORTRAIT_H;
  for(var i=0;i<panelBtns.length;i++){
    var b=panelBtns[i];
    if(px>=b.x&&px<=b.x+b.w&&py>=b.y&&py<=b.y+b.h)
      return {id:b.id, fire:mkFire(b.id), dist:(wantDist?h[0].distance:0), ext:false};
  }
  return null;
}
/* 闭包陷阱防护：必须立刻把 id 固化成参数，不能让 fire 引用循环变量 */
function mkFire(id){ return function(){ fire(id); }; }

/* 先内核面板，再委托案例自有 UI（XRCore.uiHit 注册） */
function gazeProbe(rc){
  var r=coreProbe(rc,true);
  if(r) return r;
  if(uiHitFn){
    try{
      var q=uiHitFn(rc);
      if(q && q.id && typeof q.fire==='function')
        return {id:'ui:'+q.id, fire:q.fire, dist:q.dist||0, ext:true};
    }catch(e){
      /* 不静默吞错：委托回调一旦写错（比如拿 canvas.width 去乘），注视会"看起来没反应"
         却查不到原因。这里只报一次，避免刷屏。 */
      if(!uiHitWarned){ uiHitWarned=true; try{ console.warn('[XRCore] uiHit 回调异常:', e && e.message); }catch(e2){} }
    }
  }
  return null;
}

function gazeTick(dt){
  if(!camera) return;
  if(!gRay){ gRay=new T.Raycaster(); gFrom=new T.Vector3(); gDir=new T.Vector3(); }
  camera.getWorldPosition(gFrom);
  camera.getWorldDirection(gDir);
  gRay.set(gFrom, gDir);
  var r=gazeProbe(gRay);
  var id=r? r.id : null;
  if(id && id===gazeId){
    gazeT+=dt*1000;
    gazeP=Math.min(1, gazeT/S.gazeMs);
    if(gazeT>=S.gazeMs && gazeFired!==id){
      try{ r.fire(); }catch(e){}
      S.interactions++; gazeFired=id; gazeT=0; gazeP=0;
    }
  } else {
    gazeId=id; gazeT=0; gazeP=0;
    if(!id) gazeFired=null;
  }
  retDist=r? (r.dist||0) : 0;
  retTick();
  /* 节流重绘：100ms 一次即可让进度条看起来是连续的 */
  gazeRedraw+=dt*1000;
  if(gazeRedraw>100){ gazeRedraw=0; drawPanel(); }
}

/* ---------- 主帧 ---------- */
function frame(dt){
  if(!inited) return;
  var ms=dt*1000;
  S.ms=ms; S.sessionMs+=ms; S.frames=(S.frames||0)+1;
  msHist.push(ms); if(msHist.length>900) msHist.shift();
  acc+=ms; accN++; statT+=ms;

  /* 0.5s 汇总一次 */
  if(statT>=500){
    S.msAvg=acc/accN; S.fps=Math.round(1000/S.msAvg);
    /* P99 = 最差 1% 帧（越大越卡） */
    var s=msHist.slice(-Math.min(msHist.length,600)).sort(function(a,b){return a-b;});
    S.msP99=s[Math.min(s.length-1, Math.floor(s.length*0.99))]||S.msAvg;
    acc=0; accN=0; statT=0;
    if(renderer && renderer.info){
      S.calls=renderer.info.render.calls;
      S.tris=renderer.info.render.triangles;
      S.tex=renderer.info.memory.textures;
    }
    /* 降级判定 */
    if(S.msAvg>S.budgetMs*1.20){ overT+=500; underT=0; } else if(S.msAvg<S.budgetMs*0.85){ underT+=500; overT=0; }
    else { overT=0; underT=0; }
    if(overT>=2000 && S.degrade<2){ S.degrade++; overT=0; applyDegrade(); }
    else if(underT>=4000 && S.degrade>0){ S.degrade--; underT=0; applyDegrade(); }
    if(S.sessionMs>S.restAt) S.rested=true;
    drawPanel(); updDom();
  }

  /* 眼高 */
  if(camera){
    camera.getWorldPosition(tmpV);
    S.eyeH=tmpV.y - (player? player.position.y : 0);
  }

  /* 速度 → 隧道视野（自动检测：瞬移=大位移脉冲，平滑=持续位移） */
  if(player){
    player.getWorldPosition(tmpV2);
    if(lastPos){
      var d=tmpV2.distanceTo(lastPos), v=d/Math.max(dt,0.0001);
      if(d>0.75){
        S.teleports++; S.vigTarget=0.95;     /* 瞬移：闪一下遮罩，符合 VR 惯例 */
      } else if(d>0.02 && S.moveMode==='smooth'){
        S.smoothMs+=ms; S.vigTarget=S.vignette*clamp(v/S.speed,0,1);
      } else if(d>0.02){
        S.smoothMs+=ms; S.vigTarget=S.vignette*clamp(v/S.speed,0,1)*0.6;
      } else S.vigTarget=0;
    }
    lastPos.copy(tmpV2);
  }
  S.vig=lerp(S.vig, S.vigTarget, Math.min(dt*6,1));
  if(vigMesh){
    var show=S.vig>0.02 && S.vignette>0.01;
    vigMesh.visible=show;
    vigMat.opacity=S.vig*0.92;
  }
  if(S.snapCd>0) S.snapCd-=ms;
  if(S.dashCd>0) S.dashCd-=ms;
  if(S.tips.length>1){
    S.tipT+=ms;
    if(S.tipT>9000){ S.tipT=0; S.tipIdx=(S.tipIdx+1)%S.tips.length; drawPanel(); }
  }
  if(S.gaze) gazeTick(dt); else retTick();
}

/* ---------- 统一 VR 移动（替代各例自有的 updVRMove） ---------- */
function axesOf(gp){
  if(!gp||!gp.axes) return null;
  var a=gp.axes;
  if(Math.abs(a[2]||0)>0.02 || Math.abs(a[3]||0)>0.02) return [a[2]||0, a[3]||0];
  return [a[0]||0, a[1]||0];
}
/* 单手模式：只用主手那一支手柄，另一支完全忽略——既能单手玩，也避免误触 */
function pickSources(ss){
  var arr=[], i;
  for(i=0;i<ss.inputSources.length;i++) arr.push(ss.inputSources[i]);
  if(!S.oneHand || !arr.length) return arr;
  var pick=null;
  if(S.hand!=='auto'){
    for(i=0;i<arr.length;i++) if(arr[i].handedness===S.hand){ pick=arr[i]; break; }
  }
  if(!pick){
    for(i=0;i<arr.length;i++) if(arr[i].handedness==='right'){ pick=arr[i]; break; }
  }
  if(!pick) pick=arr[0];
  return [pick];
}
function vrMove(dt){
  if(!inited||!renderer) return;
  if(!renderer.xr || !renderer.xr.isPresenting) return;
  var ss=renderer.xr.getSession(); if(!ss) return;
  var srcs=pickSources(ss);
  for(var i=0;i<srcs.length;i++){
    var gp=srcs[i].gamepad; if(!gp) continue;
    var a=axesOf(gp); if(!a) continue;
    var rx=a[0], ry=a[1];
    /* 转向 */
    if(Math.abs(rx)>0.62){
      if(S.turnMode==='snap'){
        if(S.snapCd<=0){
          player.rotation.y -= (rx>0?1:-1)*S.snapDeg*Math.PI/180;
          S.snapCd=400; S.interactions++;
          S.vigTarget=Math.max(S.vigTarget, S.vignette*0.5);
        }
      } else {
        player.rotation.y -= rx*dt*1.7;
        S.vigTarget=Math.max(S.vigTarget, S.vignette*0.35*Math.abs(rx));
      }
    }
    /* 移动 */
    if(Math.abs(ry)>0.16){
      camera.getWorldDirection(fwdV); fwdV.y=0; fwdV.normalize();
      sideV.set(-fwdV.z,0,fwdV.x);
      var sign=(ry<0)?1:-1;   /* 摇杆前推 = 前进 */
      if(S.moveMode==='teleport'){
        if(S.dashCd<=0){
          player.position.addScaledVector(fwdV, sign*S.dashDist);
          S.dashCd=460; S.teleports++; S.interactions++;
          S.vigTarget=0.95;
        }
      } else {
        player.position.addScaledVector(fwdV, sign*S.speed*dt*Math.abs(ry));
      }
    }
  }
  if(bounds && player){
    player.position.x=clamp(player.position.x, bounds.x[0], bounds.x[1]);
    player.position.z=clamp(player.position.z, bounds.z[0], bounds.z[1]);
  }
}

/* ---------- 面板点击（VR 手柄射线 + PC 鼠标 + 注视停留） ---------- */
function hitTest(rc){
  if(!panel||!rc) return null;
  var h=rc.intersectObject(panel,false);
  if(!h.length||!h[0].uv) return null;
  var px=h[0].uv.x*PORTRAIT_W, py=(1-h[0].uv.y)*PORTRAIT_H;
  for(var i=0;i<panelBtns.length;i++){
    var b=panelBtns[i];
    if(px>=b.x&&px<=b.x+b.w&&py>=b.y&&py<=b.y+b.h) return b.id;
  }
  return null;
}
function bindInput(){
  if(listenersBound||!renderer) return;
  listenersBound=true;
  /* VR 手柄：getController 返回同一对象，与案例自己的监听共存 */
  try{
    for(var i=0;i<2;i++){
      var c=renderer.xr.getController(i);
      c.addEventListener('selectstart', function(){
        var rc=new T.Raycaster();
        var o=new T.Vector3(), d=new T.Vector3(0,0,-1);
        this.getWorldPosition(o);
        d.applyQuaternion(this.getWorldQuaternion(new T.Quaternion())).normalize();
        rc.set(o,d);
        var id=hitTest(rc); if(id){ fire(id); }
      });
    }
  }catch(e){}
  /* PC 鼠标 */
  try{
    renderer.domElement.addEventListener('click', function(e){
      var rc=new T.Raycaster();
      rc.setFromCamera(new T.Vector2(e.clientX/window.innerWidth*2-1, -(e.clientY/window.innerHeight)*2+1), camera);
      var id=hitTest(rc); if(id){ fire(id); }
    });
  }catch(e){}
  /* 快捷键：单键给常用项（已避开各例占用键位）；可访问性三项用 Shift 组合，绝不冲突 */
  window.addEventListener('keydown', function(e){
    var k=(e.key||'').toLowerCase();
    if(e.shiftKey){
      if(k==='p'){ fire('pose'); }
      else if(k==='h'){ fire('hand'); }
      else if(k==='g'){ fire('gaze'); }
      return;
    }
    if(k==='v'){ fire('vig'); }
    else if(k==='b'){ fire('move'); }
    else if(k==='n'){ fire('turn'); }
    else if(k==='y'){ fire('reset'); }
  });
}

/* ---------- 对外 API ---------- */
window.XRCore={
  init:function(o){
    T=o.THREE; renderer=o.renderer; scene=o.scene; camera=o.camera; player=o.player;
    S.name=o.name||'WebXR 案例';
    if(o.targetFps){ S.targetFps=o.targetFps; S.budgetMs=Math.round(1000/o.targetFps*10)/10; }
    home=o.home||null; bounds=o.bounds||null;
    ownLocomotion=!!o.ownLocomotion; ownHook=o.ownHook||null; ownText=o.ownText||null;
    poseHook=o.onPose||null;
    S.tips=o.tips||[]; S.tipIdx=0; S.tipT=0;
    tmpV=new T.Vector3(); tmpV2=new T.Vector3();
    lastPos=new T.Vector3(); fwdV=new T.Vector3(); sideV=new T.Vector3();
    buildVignette();
    buildPanel(o.panelPos||[0,1.9,-2.0], o.panelRotY||0);
    buildDom(); bindInput();
    applyPose();
    inited=true;
  },
  frame:frame,
  vrMove:vrMove,
  fire:fire, recenter:recenter, dump:function(save){ return dump(save); }, stats:stats,
  goal:goal,
  set:function(k,v){ if(k in S){ S[k]=v; if(k==='pose') applyPose(); drawPanel(); updDom(); } },
  get:function(k){ return S[k]; },
  panel:function(){ return panel; },
  own:function(){ return ownLocomotion; },
  ui:registerUI,
  hit:function(){ S.interactions++; },
  text:function(){ return curTarget(); },
  pose:function(){ return S.pose; },
  camera:function(){ return camera; },
  player:function(){ return player; },
  rect:function(id){
    for(var i=0;i<panelBtns.length;i++) if(panelBtns[i].id===id) return panelBtns[i];
    return null;
  },
  uiNodes:function(){ return uiNodes; },
  /* 案例自有 UI 的命中委托（让注视停留也能操作案例自己的面板）：
     XRCore.uiHit(function(rc){ ... return {id:'xxx', fire:function(){}, dist:hit.distance}; }) */
  uiHit:function(fn){ uiHitFn=(typeof fn==='function')?fn:null; },
  reticle:function(){ return ret; },
  gazeProbe:function(rc){ return gazeProbe(rc); },
  /* 给 UI 面板用的参考基准高度：坐姿时整体下沉 */
  uiY:function(standingY){
    return (S.pose==='seated')? Math.max(0.72, (standingY||1.4)-0.45) : (standingY||1.4);
  },
  gazeState:function(){
    return {on:S.gaze, target:gazeId, p:gazeP, external:(typeof gazeId==='string'&&gazeId.indexOf('ui:')===0), reticle:!!(ret&&ret.visible)};
  }
};

})();
