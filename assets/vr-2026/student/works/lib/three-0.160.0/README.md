# three.js r160 · 本地化副本

这个目录里放的是 **three.js 官方发行版**的副本，只为让两位同学的作业**在断网环境下也能打开**。

| 文件 | 来源 | 说明 |
| --- | --- | --- |
| `three.module.js` | `https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js` | ES Module 版主库，1.21 MB |
| `jsm/webxr/VRButton.js` | `https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/webxr/VRButton.js` | "进入 VR"按钮组件（自带，无其它依赖） |
| `LICENSE` | `https://cdn.jsdelivr.net/npm/three@0.160.0/LICENSE` | MIT 许可原文 |

- **版本**：`r160`（`THREE.REVISION === '160'`），下载日期 2026-09-30。
- **许可**：MIT，见本目录 `LICENSE`（Copyright © 2010-2023 three.js authors）。
- **为什么放在这里**：武思夷（230812018）与汤晓婷（230812056）的作业原本用 `importmap` 指向
  jsdelivr / unpkg 上的 three，**在线有网能跑、断网或机房环境就会白屏**，也与本课"作品不要依赖外部网络"的要求冲突。
  按授课教师 2026-09-30 的决定，**只把依赖本地化，作品代码本身不动**，其余改动只有 importmap 的地址。
- **注意**：本课 `../../three.min.js` 是**另一份、更老版本的 UMD 版 three.js**（"全景漫游器"系列作品用它）。
  两种版本并存是刻意的：r160 是 ES Module，老版是全局 `THREE`，互换会直接报错。
