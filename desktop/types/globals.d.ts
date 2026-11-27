// 畫面程序的全域變數：PIXI 與 pixi-live2d-display 用真實套件型別；Cubism Core、preload 橋接（desktop／chat）與除錯探針宣告成 any。
import type * as PixiNS from 'pixi.js';
import type * as Live2D from 'pixi-live2d-display';

declare global {
  // pixi-live2d-display 0.4.0 的型別沒有涵蓋 Cubism 內部類別（動作佇列、表情、物理、著色器），這幾個宣告成 any
  const PIXI: typeof PixiNS & { live2d: typeof Live2D & { CubismMotionManager: any; CubismMotionQueueManager: any; CubismMotion: any;
    CubismExpressionMotion: any; CubismPhysics: any; CubismPose: any; CubismShader_WebGL: any } };
  const Live2DCubismCore: any;
  const desktop: any;
  const chat: any;
  interface Window { __ui: any; __chat: any; desktop: any; chat: any }
}
