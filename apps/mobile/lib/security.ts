export type StoredSession = {access_token: string; expires_at: string; session_id: string; origin: string; environment: string;
  refresh_token?:string;refresh_expires_at?:string};
export function sessionRecord(value:Omit<StoredSession,'origin'|'environment'>,origin:string,environment:string):StoredSession {
  return {access_token:value.access_token,expires_at:value.expires_at,session_id:value.session_id,origin,environment,
    ...(value.refresh_token?{refresh_token:value.refresh_token,refresh_expires_at:value.refresh_expires_at}: {})};
}
export function validStoredSession(value: unknown, origin: string, environment: string, now = Date.now()): value is StoredSession {
  if (!value || typeof value !== 'object') return false;
  const row = value as Partial<StoredSession>;
  return row.origin === origin && row.environment === environment && typeof row.access_token === 'string'
    && /^na_[A-Za-z0-9_-]{32,253}$/.test(row.access_token) && typeof row.session_id === 'string' && row.session_id.length>0
    && typeof row.expires_at === 'string' && Date.parse(row.expires_at) > now + 5000
    && Date.parse(row.expires_at)<=now+86400000+60000;
}
export function recoverableSession(value:unknown,origin:string,environment:string,now=Date.now()):value is StoredSession {
  if(validStoredSession(value,origin,environment,now))return true;
  if(!value||typeof value!=='object')return false;
  const row=value as Partial<StoredSession>;
  return row.origin===origin&&row.environment===environment&&typeof row.session_id==='string'&&row.session_id.length>0
    &&typeof row.access_token==='string'&&/^na_[A-Za-z0-9_-]{32,253}$/.test(row.access_token)
    &&typeof row.refresh_token==='string'&&/^nr_[A-Za-z0-9_-]{32,253}$/.test(row.refresh_token)
    &&typeof row.refresh_expires_at==='string'&&Date.parse(row.refresh_expires_at)>now+5000
    &&Date.parse(row.refresh_expires_at)<=now+2592000000+60000;
}
export function sessionKey(origin: string, environment: string): string {
  let hash = 2166136261;
  for (const char of `${environment}:${origin}`) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
  return `milo.session.${(hash >>> 0).toString(16)}`;
}
export function safeApiOrigin(value: string, development = false): string | null {
  try { const url = new URL(value);
    if (url.username || url.password || url.search || url.hash || !['/','','/native-api','/native-api/'].includes(url.pathname)) return null;
    if (url.protocol !== 'https:' && !(development && url.protocol === 'http:' && ['localhost','127.0.0.1','[::1]'].includes(url.hostname))) return null;
    return url.origin + (url.pathname.startsWith('/native-api') ? '/native-api' : '');
  } catch {return null;}
}
