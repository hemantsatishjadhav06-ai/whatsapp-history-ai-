import React, {createContext, useCallback, useContext, useEffect, useMemo, useRef, useState} from 'react';
import {AppState} from 'react-native';
import * as SecureStore from 'expo-secure-store';
import {ApiError, createDemoClient, reconcileSnapshot, type MiloSnapshot, type RequestOptions} from '@milo/contracts';
import {SessionGuard} from './session-guard';
import {nativeClient} from './native-api';
import type {AuthorizedSnapshot} from './private-results';
import {recoverableSession,safeApiOrigin, sessionKey, sessionRecord,validStoredSession, type StoredSession} from './security';

const environment = process.env.EXPO_PUBLIC_MILO_ENV ?? 'development';
export const apiOrigin = safeApiOrigin(process.env.EXPO_PUBLIC_API_URL ?? '', environment === 'development');
const tokenKey = sessionKey(apiOrigin ?? 'unconfigured', environment);
type Mode = 'signed-out' | 'demo' | 'live';
type AppContextValue = {mode: Mode; snapshot: AuthorizedSnapshot | null; recovering: boolean; refreshing: boolean; online: boolean;
  lastUpdated: string | null; error: string | null; pausePending: boolean; pendingPauseTarget: boolean | null;
  request<T=unknown>(path: string, options?: RequestOptions): Promise<T>; refresh(): Promise<AuthorizedSnapshot|null>; loadMore():Promise<void>; startDemo(): void;
  login(session: Omit<StoredSession,'origin'|'environment'>): Promise<void>; logout(): Promise<void>;
  pause(target: boolean): Promise<void>; draftFor(key: string): string; setDraft(key: string, value: string): void};
const Context = createContext<AppContextValue | null>(null);

export function normalizeBootstrap(value: unknown): AuthorizedSnapshot {
  const row = value as Partial<AuthorizedSnapshot> & {selected_workspace_id?: string; workspaces?: MiloSnapshot['workspaces']; user?: MiloSnapshot['user']};
  const workspace = row.workspace ?? row.workspaces?.find(item => item.id === row.selected_workspace_id) ?? row.workspaces?.[0];
  if (!row.user) throw new Error('No authenticated owner is available');
  const resolvedWorkspace=workspace??{id:'',name:'Create your workspace',timezone:'Asia/Kolkata',paused:true,pause_generation:0};
  return {user: row.user, workspace:resolvedWorkspace, workspaces: row.workspaces ?? (workspace?[workspace]:[]), simulation: row.simulation??false,
    connections: row.connections ?? [], conversations: row.conversations ?? [], messages: row.messages ?? [],
    actions: row.actions ?? [], drafts: row.drafts ?? [], tasks: row.tasks ?? [], jobs: row.jobs ?? [], memories: row.memories ?? [],
    styles: row.styles ?? [], grants: row.grants ?? [], routes: row.routes ?? [], activity: row.activity ?? [], contacts: row.contacts ?? [],
    budget: row.budget ?? null, retention: row.retention ?? null, pagination:row.pagination,
    snapshot_version:row.snapshot_version,generated_at: row.generated_at ?? new Date().toISOString()};
}

export function AppProvider({children}: {children: React.ReactNode}) {
  const [mode,setMode] = useState<Mode>('signed-out');
  const [snapshot,setSnapshot] = useState<AuthorizedSnapshot|null>(null);
  const [recovering,setRecovering] = useState(true);
  const [refreshing,setRefreshing] = useState(false);
  const [online,setOnline] = useState(true);
  const [error,setError] = useState<string|null>(null);
  const [lastUpdated,setLastUpdated] = useState<string|null>(null);
  const [pausePending,setPausePending] = useState(false);
  const [pendingPauseTarget,setPendingPauseTarget] = useState<boolean|null>(null);
  const session = useRef<StoredSession|null>(null);
  const demo = useRef(createDemoClient());
  const drafts = useRef(new Map<string,string>());
  const pauseKey = useRef<string|null>(null);
  const guard=useRef(new SessionGuard());
  const refreshFlight=useRef<Promise<StoredSession|null>|null>(null);
  const pageFlight=useRef<string|null>(null);
  const clearPrivateState = useCallback(() => {
    guard.current.invalidate(); session.current=null; setSnapshot(null); setMode('signed-out'); drafts.current.clear();
    setPausePending(false); setPendingPauseTarget(null); pauseKey.current=null;
    refreshFlight.current=null;
    pageFlight.current=null;
    return guard.current.enqueueStorage(()=>SecureStore.deleteItemAsync(tokenKey)).catch(()=>setError('Local secure session removal failed. Retry sign-out.'));
  },[]);
  const live = useMemo(() => apiOrigin ? nativeClient(apiOrigin) : null,[]);
  const renewSession=useCallback(async():Promise<StoredSession|null>=>{
    if(refreshFlight.current)return refreshFlight.current;
    const current=session.current;const startedEpoch=guard.current.epoch;
    if(!current?.refresh_token||!apiOrigin||!recoverableSession(current,apiOrigin,environment)){await clearPrivateState();return null;}
    const flight=(async()=>{let issuedAccess:string|undefined;try {
      const result=await nativeClient(apiOrigin).request<Omit<StoredSession,'origin'|'environment'>>('/v1/auth/native/refresh',
        {method:'POST',body:{refresh_token:current.refresh_token}});
      const next=sessionRecord(result,apiOrigin,environment);
      issuedAccess=next.access_token;
      if(!validStoredSession(next,apiOrigin,environment))throw new Error('The server returned an invalid renewed session');
      if(startedEpoch!==guard.current.epoch){await nativeClient(apiOrigin,next.access_token)
        .request('/v1/auth/native/logout',{method:'POST'}).catch(()=>{});return null;}
      await guard.current.enqueueStorage(()=>SecureStore.setItemAsync(tokenKey,JSON.stringify(next),{keychainAccessible:SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY}),startedEpoch);if(startedEpoch!==guard.current.epoch){await nativeClient(apiOrigin,next.access_token).request('/v1/auth/native/logout',{method:'POST'}).catch(()=>{});return null;}session.current=next;return next;
    } catch(error){if(issuedAccess)await nativeClient(apiOrigin,issuedAccess).request('/v1/auth/native/logout',{method:'POST'}).catch(()=>{});
      if(startedEpoch===guard.current.epoch)await clearPrivateState();throw error;}})();
    refreshFlight.current=flight;try{return await flight;}finally{if(refreshFlight.current===flight)refreshFlight.current=null;}
  },[clearPrivateState]);
  const request = useCallback(async <T,>(path: string, options?: RequestOptions): Promise<T> => {
    const startedEpoch=guard.current.epoch;const startedRead=guard.current.readSequence;
    if (mode === 'demo') {const result=await demo.current.request<T>(path,options);if(!guard.current.matches(startedEpoch,startedRead))throw new ApiError(409,'SESSION_CHANGED','Authorized context changed');if((options?.method??'GET')!=='GET')guard.current.invalidateReads();return result;}
    if (!live) throw new Error('Configure a trusted HTTPS API origin to connect this app');
    if (session.current && !validStoredSession(session.current,apiOrigin!,environment)) {
      const next=await renewSession();if(!next)throw new Error('Session expired. Sign in again.');}
    if(startedEpoch!==guard.current.epoch)throw new ApiError(409,'SESSION_CHANGED','Account context changed');
    if(startedRead!==guard.current.readSequence)throw new ApiError(409,'SCOPE_CHANGED','Authorized state changed. Refresh current evidence before proceeding.');
    let result:T;
    try{result=await nativeClient(apiOrigin!,session.current?.access_token).request<T>(path,options);}catch(error){
      if(error instanceof ApiError&&error.status===401&&startedEpoch===guard.current.epoch){setSnapshot(null);
        const renewed=await renewSession();
        if(renewed&&(options?.method??'GET')==='GET'){try{result=await nativeClient(apiOrigin!,renewed.access_token).request<T>(path,options);}catch(retryError){if(retryError instanceof ApiError&&retryError.status===401&&startedEpoch===guard.current.epoch)await clearPrivateState();throw retryError;}}
        else throw new ApiError(401,'SESSION_RENEWED','Sign-in state changed. Review and explicitly retry this request.');
      }else throw error;
    }
    if(startedEpoch!==guard.current.epoch)throw new ApiError(409,'SESSION_CHANGED','Account context changed');
    if(startedRead!==guard.current.readSequence)throw new ApiError(409,'SCOPE_CHANGED','Authorized state changed. Refresh to review the current operation receipt; this request was not replayed.');
    if((options?.method??'GET')!=='GET')guard.current.invalidateReads();
    return result;
  },[mode,live,renewSession]);
  const refresh = useCallback(async () => {
    if (mode === 'signed-out') return null;
    const startedEpoch=guard.current.epoch; const sequence=++guard.current.readSequence; setRefreshing(true);
    try {
      const next=mode==='demo' ? demo.current.snapshot() : normalizeBootstrap(await request('/v1/ui/bootstrap'));
      if(startedEpoch!==guard.current.epoch||sequence!==guard.current.readSequence) return null;
      const allowedChats=new Set(next.conversations.map(row=>row.id));
      for(const key of drafts.current.keys()){if(!allowedChats.has(decodeURIComponent(key.split('|')[2]??'')))drafts.current.delete(key);}
      setSnapshot(previous=>reconcileSnapshot(previous,next)); setOnline(true); setError(null); setLastUpdated(new Date().toISOString());
      return next;
    } catch(error) {if(startedEpoch===guard.current.epoch&&sequence===guard.current.readSequence) {setOnline(false); setError(error instanceof Error?error.message:'Refresh unavailable');}return null;}
    finally {if(startedEpoch===guard.current.epoch&&sequence===guard.current.readSequence)setRefreshing(false);}
  },[mode,request]);
  const loadMore=async()=>{const cursor=snapshot?.pagination?.conversation_next_cursor;if(!cursor||!snapshot)return;
    const startedEpoch=guard.current.epoch;const workspaceId=snapshot.workspace.id;const sequence=guard.current.readSequence;
    const key=`${startedEpoch}:${sequence}:${workspaceId}:${cursor}`;if(pageFlight.current===key)return;pageFlight.current=key;
    try{const next=normalizeBootstrap(await request(`/v1/ui/bootstrap?workspace_id=${encodeURIComponent(workspaceId)}&conversation_cursor=${encodeURIComponent(cursor)}`));
    if(!guard.current.matches(startedEpoch,sequence)||next.workspace.id!==workspaceId)return;
    const merge=<T extends {id:string}>(left:T[],right:T[])=>Array.from(new Map([...left,...right].map(row=>[row.id,row])).values());
    setSnapshot(previous=>previous?.workspace.id===workspaceId?{...next,
      conversations:merge(previous.conversations,next.conversations),actions:merge(previous.actions,next.actions),
      drafts:merge(previous.drafts,next.drafts),memories:merge(previous.memories,next.memories),styles:merge(previous.styles,next.styles),
      grants:merge(previous.grants,next.grants),routes:merge(previous.routes,next.routes),tasks:merge(previous.tasks,next.tasks),jobs:merge(previous.jobs,next.jobs)}:next);
    }catch(error){if(guard.current.matches(startedEpoch,sequence)){setError(error instanceof Error?error.message:'Page unavailable');setOnline(false);}}
    finally{if(pageFlight.current===key)pageFlight.current=null;}
  };
  useEffect(()=>{let active=true;const startedEpoch=guard.current.epoch;
    (async()=>{try {const raw=await SecureStore.getItemAsync(tokenKey); const stored=raw?JSON.parse(raw):null;
      if(apiOrigin && recoverableSession(stored,apiOrigin,environment) && live) {
        if(!active||startedEpoch!==guard.current.epoch)return;
        session.current=stored;
        if(!validStoredSession(stored,apiOrigin,environment))await renewSession();
        if(!active||startedEpoch!==guard.current.epoch||!session.current)return;
        const next=normalizeBootstrap(await nativeClient(apiOrigin,session.current.access_token).request('/v1/ui/bootstrap'));
        if(active&&startedEpoch===guard.current.epoch){setSnapshot(next);setMode('live');setLastUpdated(new Date().toISOString());}
      } else if(raw&&startedEpoch===guard.current.epoch) {await guard.current.enqueueStorage(()=>SecureStore.deleteItemAsync(tokenKey),startedEpoch);}
    } catch {if(active&&startedEpoch===guard.current.epoch) {setError('Session recovery needs a new sign-in. Private content remains hidden.');void clearPrivateState();}}
    finally{if(active)setRecovering(false);}})(); return()=>{active=false;};
  },[live,clearPrivateState,renewSession]);
  useEffect(()=>{const listener=AppState.addEventListener('change',state=>{if(state==='active')void refresh();});
    const timer=setInterval(()=>{if(AppState.currentState==='active')void refresh();},30000);
    return()=>{listener.remove();clearInterval(timer);};},[refresh]);
  const startDemo=()=>{clearPrivateState();demo.current=createDemoClient();setSnapshot(demo.current.snapshot());setMode('demo');setRecovering(false);setOnline(true);setError(null);setLastUpdated(new Date().toISOString());};
  const login=async(value:Omit<StoredSession,'origin'|'environment'>)=>{
    if(!apiOrigin||!live)throw new Error('A trusted API origin is required');
    const removal=clearPrivateState();const startedEpoch=guard.current.epoch;await removal;
    if(startedEpoch!==guard.current.epoch){await nativeClient(apiOrigin,value.access_token).request('/v1/auth/native/logout',{method:'POST'}).catch(()=>{});return;}
    const next=sessionRecord(value,apiOrigin,environment);
    if(!validStoredSession(next,apiOrigin,environment))throw new Error('The backend returned an invalid or expired session');
    try{
      await guard.current.enqueueStorage(()=>SecureStore.setItemAsync(tokenKey,JSON.stringify(next),{keychainAccessible:SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY}),startedEpoch);
      if(startedEpoch!==guard.current.epoch){await nativeClient(apiOrigin,next.access_token).request('/v1/auth/native/logout',{method:'POST'}).catch(()=>{});return;}
      session.current=next;
      const data=normalizeBootstrap(await nativeClient(apiOrigin,next.access_token).request('/v1/ui/bootstrap'));if(startedEpoch===guard.current.epoch){setSnapshot(data);setMode('live');setError(null);setLastUpdated(new Date().toISOString());}}
    catch(error){await nativeClient(apiOrigin,next.access_token).request('/v1/auth/native/logout',{method:'POST'}).catch(()=>{});
      if(startedEpoch===guard.current.epoch)await clearPrivateState();throw error;}
  };
  const logout=async()=>{const current=session.current;const currentMode=mode;const removal=clearPrivateState();const startedEpoch=guard.current.epoch;await removal;
    if(currentMode==='live'&&current&&apiOrigin){try{
      if(current.refresh_token)await nativeClient(apiOrigin).request('/v1/auth/native/revoke',{method:'POST',body:{refresh_token:current.refresh_token}});
      else await nativeClient(apiOrigin,current.access_token).request('/v1/auth/native/logout',{method:'POST'});}
      catch{if(guard.current.matches(startedEpoch))setError('Local data cleared. Server sign-out was not confirmed; revoke this device from another signed-in client.');}}
  };
  const pause=async(target:boolean)=>{
    if(!snapshot)throw new Error('Authenticate and load a workspace first');
    if(pendingPauseTarget!==target||!pauseKey.current)pauseKey.current=`native-control-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setPendingPauseTarget(target);setPausePending(true);
    const startedEpoch=guard.current.epoch;const workspaceId=snapshot.workspace.id;
    try {const receipt=await request<{paused:boolean;pause_generation:number}>(`/v1/${target?'pause':'resume'}-all?workspace_id=${encodeURIComponent(snapshot.workspace.id)}`,{method:'POST',idempotencyKey:pauseKey.current});
      if(receipt.paused!==target||typeof receipt.pause_generation!=='number')throw new Error('Control acknowledgement unavailable');
      if(startedEpoch!==guard.current.epoch)return;
      setSnapshot(previous=>previous?.workspace.id===workspaceId?{...previous,workspace:{...previous.workspace,paused:target,pause_generation:receipt.pause_generation}}:previous);
      setPausePending(false);setPendingPauseTarget(null);pauseKey.current=null;setOnline(true);await refresh();
    } catch(error){if(startedEpoch===guard.current.epoch){setOnline(false);setError(target?'Pause not confirmed; automation may still be active':'Resume not confirmed; fetch current server state before trying again');}throw error;}
  };
  return <Context.Provider value={{mode,snapshot,recovering,refreshing,online,lastUpdated,error,pausePending,pendingPauseTarget,
    request,refresh,loadMore,startDemo,login,logout,pause,draftFor:key=>drafts.current.get(key)??'',setDraft:(key,value)=>{drafts.current.set(key,value);}}}>{children}</Context.Provider>;
}
export function useMilo(){const value=useContext(Context);if(!value)throw new Error('Milo context unavailable');return value;}
