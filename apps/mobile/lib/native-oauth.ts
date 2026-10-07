import type {ApiClient} from '@milo/contracts';
import type {StoredSession} from './security';

export const NATIVE_APP_REDIRECT = 'milo://oauth';
/** Expo Router must leave the in-flight sign-in screen mounted on auth return. */
export function nativeIntentPath(path:string):string {
  try {const url=new URL(path);if(url.protocol==='milo:'&&url.hostname==='oauth')return '/sign-in';}
  catch {/* Ordinary internal router paths are not absolute URLs. */}
  return path;
}
export type NativeGoogleConfig = {client_id:string|null;native_broker_configured?:boolean;
  native_platforms?:{ios:boolean;android:boolean}};
export function nativeGoogleReady(config:NativeGoogleConfig|undefined,platform:string):boolean {
  return (platform==='ios'||platform==='android')&&config?.native_broker_configured===true
    &&config.native_platforms?.[platform]===true&&!!config.client_id;
}

/** The system browser may open only the fixed Google endpoint and our callback. */
export function trustedGoogleAuthorization(value:string,apiBase:string,clientId:string):string {
  const url=new URL(value);const redirect=new URL(url.searchParams.get('redirect_uri')??'');
  if(url.origin!=='https://accounts.google.com'||url.pathname!=='/o/oauth2/v2/auth'||url.username||url.password||url.hash
    ||url.searchParams.get('client_id')!==clientId||url.searchParams.get('response_type')!=='code'
    ||url.searchParams.get('code_challenge_method')!=='S256'||!url.searchParams.get('state')
    ||redirect.origin!==new URL(apiBase).origin||redirect.pathname!=='/api/auth/native/google/callback'
    ||redirect.username||redirect.password||redirect.search||redirect.hash)throw new Error('The sign-in service returned an untrusted authorization destination');
  for(const key of url.searchParams.keys())if(url.searchParams.getAll(key).length!==1)throw new Error('The authorization request is ambiguous');
  return url.href;
}

/** No auth code, token, fragment, alternate URI or duplicate parameter is accepted. */
export function nativeHandoff(value:string):string {
  const url=new URL(value);
  if(`${url.protocol}//${url.host}${url.pathname}`!==NATIVE_APP_REDIRECT||url.username||url.password||url.hash)
    throw new Error('The sign-in response returned to an unexpected app destination');
  const keys=[...url.searchParams.keys()];
  if(keys.length!==1||url.searchParams.getAll(keys[0]).length!==1)
    throw new Error('The sign-in response is ambiguous');
  if(keys[0]==='error')throw new Error(url.searchParams.get('error')==='access_denied'?'Sign-in canceled. You can try again.':'Google sign-in was not completed. Try again.');
  const handoff=url.searchParams.get('handoff');
  if(keys[0]!=='handoff'||!handoff||!/^nh_[A-Za-z0-9_-]{32,253}$/.test(handoff))
    throw new Error('The sign-in service did not return a valid app handoff');
  return handoff;
}

type BrowserResult={type:string;url?:string};
export async function authenticateNativeGoogle(options:{api:ApiClient;apiBase:string;clientId:string;
  platform:'ios'|'android';proof:{codeVerifier:string;codeChallenge:string};
  openBrowser:(url:string,redirect:string)=>Promise<BrowserResult>;isActive:()=>boolean;
  revoke:(token:string)=>Promise<unknown>}):Promise<Omit<StoredSession,'origin'|'environment'>|null>{
  const started=await options.api.request<{authorization_url:string;app_redirect_uri:string;expires_at:string}>
    ('/v1/auth/native/google/start',{method:'POST',body:{platform:options.platform,
      device_name:`Milo ${options.platform}`,code_challenge:options.proof.codeChallenge}});
  if(!options.isActive())return null;
  if(started.app_redirect_uri!==NATIVE_APP_REDIRECT||!(Date.parse(started.expires_at)>Date.now()))
    throw new Error('The sign-in challenge is invalid or expired');
  const url=trustedGoogleAuthorization(started.authorization_url,options.apiBase,options.clientId);
  const response=await options.openBrowser(url,NATIVE_APP_REDIRECT);
  if(!options.isActive())return null;
  if(response.type==='cancel'||response.type==='dismiss')throw new Error('Sign-in canceled. You can try again.');
  if(response.type!=='success'||!response.url)throw new Error('Google did not return a valid sign-in response');
  const session=await options.api.request<Omit<StoredSession,'origin'|'environment'>>('/v1/auth/native/google/exchange',
    {method:'POST',body:{handoff:nativeHandoff(response.url),code_verifier:options.proof.codeVerifier}});
  if(!options.isActive()){await options.revoke(session.access_token).catch(()=>{});return null;}
  return session;
}
