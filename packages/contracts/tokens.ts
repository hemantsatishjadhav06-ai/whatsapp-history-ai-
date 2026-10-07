export const tokens = {
  colors: {canvas: '#FAF7F2', surface: '#FFFFFF', ink: '#2C2538', secondary: '#6E6578',
    primary: '#6740C8', primaryText: '#FFFFFF', selected: '#EAE0FD', peach: '#F6C7AA', border: '#E3DCEB',
    caution: '#FEEDD1', cautionText: '#754915', success: '#E4F3ED', successText: '#225844',
    danger: '#FBE4E7', dangerText: '#8E2440'},
  space: [4, 8, 12, 16, 20, 24, 32, 40, 48],
  radii: {card: 24, panel: 16, control: 16, pill: 99},
  fonts: {heading: 'SpaceGrotesk_700Bold', body: 'DMSans_400Regular', medium: 'DMSans_500Medium'},
  touchTarget: 48,
} as const;
export const localization = {en: {home: 'Home', inbox: 'Inbox', actions: 'Actions', memory: 'Memory', more: 'More',
  askMilo: 'Ask Milo', pauseUnconfirmed: 'Pause not confirmed; automation may still be active',
  synthetic: 'Synthetic demo · no accounts connected or messages sent'}} as const;
