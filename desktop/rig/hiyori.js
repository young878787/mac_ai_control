// Live2D 官方範例模型 hiyori_pro_t11 的 profile（ADR 0032、0033）：只寫和約定不同的地方。模型沒有表情檔，契約裡表情與手勢本來就選用。
// 動作用 model3.json 各群組的第一個；對應依動作曲線挑的（頭部擺動方向、嘴、眼、手臂）。
export default {
  motions: { surprised: 'Tap', curious: 'Flick', nod: 'Tap@Body', shake: 'FlickDown', happy: 'FlickUp', talk: 'Flick@Body' },
  once: ['surprised', 'curious', 'nod', 'shake', 'happy'],   // 動作檔全部標 Loop，這幾個反應只播一次才回得到待機
  layout: { region: { x: 634, y: 200, w: 830, h: 2610 }, height: 420, headFraction: 0.19 },   // region 以「模型畫布寬＝2048」的座標表示（animator.js）
};
