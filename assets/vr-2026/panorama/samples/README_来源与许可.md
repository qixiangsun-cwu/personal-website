# 本目录：官方 WebXR Samples 的本地化副本

## 来源

| 项 | 内容 |
| --- | --- |
| 上游仓库 | `immersive-web/webxr-samples`（W3C 沉浸式 Web 社区组） |
| 在线站点 | <https://immersive-web.github.io/webxr-samples/> |
| 许可 | **MIT**（可自由使用、修改、再分发，**须保留版权声明**） |
| 核对日期 | 2026-09-29 |

> 版权声明本身就在每个 HTML 文件顶部的注释里，**随文件一起保留，不要删除**。
> 这些是官方样例，**不是本课程原创**；放进课程页面时请保留出处。

## 为什么只放这一个

官方站点共 **23 个样例 / 7 类**。本课程**只本地化真正要用的**，其余用官方在线地址（https，头显可直接打开）。
选它的原因是它在官方分类里属于 **No Dependencies**：

| 项 | 值 |
| --- | --- |
| 文件大小 | **7.7 KB（单文件）** |
| 外部依赖 | **0 个 import、0 个素材** |
| 渲染后端 | **WebGL**（`getContext('webgl', {xrCompatible:true})`）——不是 WebGPU |
| 它做什么 | 进入 `immersive-vr` 会话后，把显示清成**缓慢变化的颜色**，用来证明会话与渲染已经跑起来 |

所以它适合当**"第一性"演示**：如果连它都进不去 VR，问题一定在**环境**（安全上下文 / 浏览器 / 设备），
而**不在学生的代码**里。这正好是本课要反复讲的那句话——**先怀疑环境，再怀疑代码。**

## ⚠️ 一个必须避开的东西

官方样例分两套写法：

| 形态 | 例 | 能不能用 |
| --- | --- | --- |
| **普通版**（WebGL） | `vr-barebones.html`、`360-photos.html`、`stereo-video.html` … | ✅ 头显上能跑 |
| **`*-webgpu.html` 版** | `immersive-vr-session-webgpu.html` 等 | ❌ **要 `navigator.gpu`，没有 WebGL 回退，PICO Neo 3 跑不了** |

> 本课次目录里那个 `_webgpu_offline\` 文件夹就是 **WebGPU 版**，而且它只有页面、没有 `js\` 依赖树，
> **当"能跑的演示"不成立**。详见《第5次课_内容设计_v2_带案例演示.md》第 4 节。

## 其余样例怎么用（不本地化）

第 5 章真正用得到、但**不必本地化**的几条，直接给官方在线地址（`https` 打开即安全上下文，头显可进 VR）：

| 样例 | 官方地址 | 用在第 5 章哪一段 |
| --- | --- | --- |
| 360 Stereo Photos | <https://immersive-web.github.io/webxr-samples/360-photos.html> | 5.1 —— **官方实现的全景播放器**（本章最对口的一条） |
| Stereo Video | <https://immersive-web.github.io/webxr-samples/stereo-video.html> | 5.1 —— 讲"全景视频 vs VR 视频" |
| Positional Audio | <https://immersive-web.github.io/webxr-samples/positional-audio.html> | 备选：空间音频 |
| Teleportation | <https://immersive-web.github.io/webxr-samples/teleportation.html> | 5.3 —— 下次课"多点漫游"的地基 |
| Input Selection | <https://immersive-web.github.io/webxr-samples/input-selection.html> | 5.3 —— "点热点"的通用做法 |

目录页：<https://immersive-web.github.io/webxr-samples/>

> 这几条都依赖官方仓库的 `js\` 树（`immersive-vr-session` 还需要 `gltf` 模型与银河贴图）。
> **要离线就得把整棵 `js\` 拖下来**；上游整仓 **436 MB**（大头是 media 视频与模型），**不要整仓拉**。
> 如果决定本地化，按《内容设计 v2》第 6 节的顺序做（ⓐⓑⓒ 共用同一棵 `js\`）。
