// 所有模型共用的契約（ADR 0033）：狀態機的每個語意名稱 → 約定的模型名稱，以及程序層用的標準參數 ID。
// 等級以類別為單位：動作、參數必要（缺了拋錯）；表情、手勢選用（缺了只警告、不演）。profile 只能改名稱與版面，改不了等級。
// 約定名稱沿用 RushiaHD（runtime-fullrig03）：照這些名稱做的模型不用寫 profile。
export const CONTRACT = Object.freeze({
  motions: { idle: 'Idle', surprised: 'Surprised', curious: 'Curious', nod: 'Nod', shake: 'Shake', happy: 'Happy', talk: 'Talk' },   // 不用 Blink：它會把 Idle 的姿勢歸零
  expressions: { neutral: 'neutral', happy: 'happy', pout: 'pout', sleepy: 'sleepy', surprised: 'surprised', starEyes: 'star_eyes', poutLeft: 'pout_left' },
  gestures: { armLeftUp: 'arm_l_up', armRightUp: 'arm_r_up', chinRest: 'chin_rest' },   // 手勢層（手臂切換參數），和臉部表情分開排隊
  params: { angleX: 'ParamAngleX', angleY: 'ParamAngleY', angleZ: 'ParamAngleZ', bodyZ: 'ParamBodyAngleZ',
    eyeX: 'ParamEyeBallX', eyeY: 'ParamEyeBallY', eyeOpen: 'ParamEyeLOpen',
    bodyX: 'ParamBodyAngleX', bodyY: 'ParamBodyAngleY', eyeROpen: 'ParamEyeROpen',                                                    // 以下是程序層（擬人化）用的參數
    browLY: 'ParamBrowLY', browRY: 'ParamBrowRY', browLX: 'ParamBrowLX', browRX: 'ParamBrowRX',
    browLForm: 'ParamBrowLForm', browRForm: 'ParamBrowRForm', browLAngle: 'ParamBrowLAngle', browRAngle: 'ParamBrowRAngle', breath: 'ParamBreath' },
});
export const REQUIRED = Object.freeze(['motions', 'params']);   // 其餘類別選用

// 沒有 profile 時的版面：region 用整張畫布（checkRig 依畫布比例算），出場高度同 RushiaHD；headFraction 取 RushiaHD（0.30）與 hiyori（0.19）之間。
// headCenter＝頭部中心離模型頂端的比例（游標、點擊的 dy 以它為基準），profile 的 layout 沒寫就用這個（RushiaHD 的值，ADR 0034）
export const DEFAULT_LAYOUT = Object.freeze({ height: 420, headFraction: 0.25, headCenter: 0.16 });
