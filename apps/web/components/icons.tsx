import type { CSSProperties } from 'react';

const paths: Record<string, React.ReactNode> = {
  home: <><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z" /></>,
  inbox: <><rect x="3" y="4" width="18" height="16" rx="4"/><path d="M3 13h5l2 3h4l2-3h5"/></>,
  actions: <><rect x="4" y="5" width="16" height="16" rx="3"/><path d="M8 3v4m8-4v4M4 11h16m-12 4h3m2 0h3"/></>,
  memory: <><path d="M12 21s-8-4-8-10a4 4 0 0 1 8-3 4 4 0 0 1 8 3c0 6-8 10-8 10Z"/></>,
  rules: <><path d="M5 6h14M5 12h14M5 18h14"/><circle cx="9" cy="6" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="10" cy="18" r="2"/></>,
  connections: <><path d="m10 14 4-4m-6 5-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m4 2 2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 0)"/></>,
  activity: <><path d="M3 12h4l3-8 4 16 3-8h4"/></>,
  settings: <><path d="m10 3-1 3-3 1-3 3 2 2-1 3 3 3 3-1 2 2 3-1 1-3 3-1 1-3-2-2 1-3-3-2-3 1Z"/><circle cx="12" cy="12" r="3"/></>,
  search: <><circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/></>,
  arrow: <path d="M4 12h15m-6-6 6 6-6 6"/>,
  chevron: <path d="m9 5 7 7-7 7"/>,
  close: <path d="m6 6 12 12M18 6 6 18"/>,
  check: <path d="m5 12 4 4L19 6"/>,
  pause: <><path d="M8 5v14M16 5v14" strokeWidth="4"/></>,
  play: <path d="m8 4 12 8-12 8Z"/>,
  mic: <><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2m-7 9v3m-3 0h6"/></>,
  send: <><path d="m3 3 18 9-18 9 3-9Zm3 9h15"/></>,
  more: <><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></>,
  whatsapp: <><path d="M20 11a8 8 0 0 1-12 7l-5 2 1-5a8 8 0 1 1 16-4Z"/><path d="M8 7c0 4 2 6 6 7l2-2-3-2-1 1-2-2 1-1-2-2Z"/></>,
  shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/></>,
  sparkles: <><path d="m12 3 2 6 6 3-6 2-2 7-2-7-6-2 6-3Z"/><path d="m20 2 1 2 2 1-2 1-1 2-1-2-2-1 2-1Z"/></>,
  back: <path d="m14 5-7 7 7 7"/>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-11v1"/></>,
};
export function Icon({ name, size = 20, className = '' }: { name: string; size?: number; className?: string }) {
  return <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] ?? paths.sparkles}</svg>;
}
export function Milo({ size = 76, className = '', style }: { size?: number; className?: string; style?: CSSProperties }) {
  return <svg className={`milo-mascot ${className}`} width={size} height={size} viewBox="0 0 100 100" fill="none" role="img" aria-label="Milo companion" style={style}>
    <path d="M26 18c10-9 18-4 24-1 14-8 32 1 31 15 13 13 11 31-1 35 1 18-18 22-32 17-18 7-35-1-35-17-12-9-10-26 0-32-3-11 2-16 13-17Z" fill="#EAE0FD"/>
    <circle cx="78" cy="20" r="14" fill="#F6C7AA"/><ellipse cx="36" cy="47" rx="3" ry="5" fill="#2C2538"/><ellipse cx="61" cy="47" rx="3" ry="5" fill="#2C2538"/>
    <path d="M37 61c7 7 16 7 23 0" stroke="#2C2538" strokeWidth="2.5" strokeLinecap="round"/>
  </svg>;
}
