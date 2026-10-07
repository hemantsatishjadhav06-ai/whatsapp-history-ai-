import test from 'node:test';
import assert from 'node:assert/strict';
import type {ApiClient,RequestOptions} from '@milo/contracts';
import {authenticateNativeGoogle,nativeGoogleReady,nativeHandoff,nativeIntentPath,trustedGoogleAuthorization} from '../lib/native-oauth';

const apiBase='https://milo.example/native-api';
const clientId='synthetic-web-client';
const callback='https://milo.example/api/auth/native/google/callback';
const handoff='nh_synthetic_handoff_example_1234567890';
const authorize='https://accounts.google.com/o/oauth2/v2/auth?'+new URLSearchParams({client_id:clientId,
  redirect_uri:callback,response_type:'code',state:'synthetic-state',code_challenge_method:'S256'});
const session={access_token:'na_synthetic_access_example_1234567890',expires_at:new Date(Date.now()+3600000).toISOString(),session_id:'synthetic-session'};
const proof={codeVerifier:'a'.repeat(43),codeChallenge:'b'.repeat(43)};
function transport(onExchange?:()=>void){const requests:Array<{path:string;body:unknown}>=[];
  const api:ApiClient={async request<T>(path:string,options?:RequestOptions){requests.push({path,body:options?.body});
    if(path.endsWith('/start'))return {authorization_url:authorize,app_redirect_uri:'milo://oauth',expires_at:new Date(Date.now()+300000).toISOString()} as T;
    onExchange?.();return session as T;}};return {api,requests};}

test('native availability selects the installed platform and requires the HTTPS broker',()=>{
  const config={client_id:clientId,native_broker_configured:true,native_platforms:{ios:true,android:false}};
  assert.equal(nativeGoogleReady(config,'ios'),true);
  assert.equal(nativeGoogleReady(config,'android'),false);
  assert.equal(nativeGoogleReady(config,'web'),false);
  assert.equal(nativeGoogleReady({...config,native_broker_configured:false},'ios'),false);
});

test('native broker opens only Google with an HTTPS callback at its configured API host',()=>{
  assert.equal(trustedGoogleAuthorization(authorize,apiBase,clientId),authorize);
  const wrongCallback=new URL(authorize);wrongCallback.searchParams.set('redirect_uri','https://attacker.example/api/auth/native/google/callback');
  const customCallback=new URL(authorize);customCallback.searchParams.set('redirect_uri','milo://oauth');
  const wrongAudience=new URL(authorize);wrongAudience.searchParams.set('client_id','another-client');
  for(const url of [authorize.replace('accounts.google.com','attacker.example'),wrongCallback.href,customCallback.href,wrongAudience.href,authorize+'&client_id=another',authorize+'#token'])
    assert.throws(()=>trustedGoogleAuthorization(url,apiBase,clientId));
});

test('native redirects accept exactly one opaque handoff and reject token leakage or alternate destinations',()=>{
  assert.equal(nativeHandoff(`milo://oauth?handoff=${handoff}`),handoff);
  for(const url of [`other://oauth?handoff=${handoff}`,`milo://other?handoff=${handoff}`,`milo://oauth/path?handoff=${handoff}`,
    `milo://oauth?handoff=${handoff}&code=google-code`,`milo://oauth?handoff=${handoff}&handoff=${handoff}`,
    `milo://oauth?handoff=${handoff}#token`,`milo://oauth?id_token=google-token`,`milo://oauth?handoff=na_access-token`])assert.throws(()=>nativeHandoff(url));
  assert.throws(()=>nativeHandoff('milo://oauth?error=access_denied'),/canceled/);
  assert.throws(()=>nativeHandoff('milo://oauth?error=private-provider-details'),/not completed/);
});

test('native browser return keeps the sign-in route while cold-start links cannot recreate a proof',()=>{
  assert.equal(nativeIntentPath(`milo://oauth?handoff=${handoff}`),'/sign-in');
  assert.equal(nativeIntentPath('milo://oauth?error=access_denied'),'/sign-in');
  assert.equal(nativeIntentPath('/detail/conversation/chat-a'),'/detail/conversation/chat-a');
  assert.equal(nativeIntentPath('milo://detail/conversation/chat-a'),'milo://detail/conversation/chat-a');
});

test('native sign-in performs one explicit exchange and never returns Google tokens through app links',async()=>{
  const {api,requests}=transport();const opened:Array<[string,string]>=[];
  const result=await authenticateNativeGoogle({api,apiBase,clientId,platform:'android',proof,
    openBrowser:async(url,redirect)=>{opened.push([url,redirect]);return {type:'success',url:`milo://oauth?handoff=${handoff}`};},
    isActive:()=>true,revoke:async()=>{throw new Error('Must not revoke a current login');}});
  assert.deepEqual(result,session);assert.deepEqual(opened,[[authorize,'milo://oauth']]);
  assert.deepEqual(requests,[{path:'/v1/auth/native/google/start',body:{platform:'android',device_name:'Milo android',code_challenge:proof.codeChallenge}},
    {path:'/v1/auth/native/google/exchange',body:{handoff,code_verifier:proof.codeVerifier}}]);
});

test('cancel, hostile return and owner navigation cannot exchange a pending native challenge',async()=>{
  for(const response of [{type:'cancel'},{type:'dismiss'},{type:'success',url:`attacker://oauth?handoff=${handoff}`}]){
    const {api,requests}=transport();await assert.rejects(authenticateNativeGoogle({api,apiBase,clientId,platform:'ios',proof,
      openBrowser:async()=>response,isActive:()=>true,revoke:async()=>{}}));assert.equal(requests.length,1);
  }
  const {api,requests}=transport();let active=true;
  const result=await authenticateNativeGoogle({api,apiBase,clientId,platform:'ios',proof,
    openBrowser:async()=>{active=false;return {type:'success',url:`milo://oauth?handoff=${handoff}`};},isActive:()=>active,revoke:async()=>{}});
  assert.equal(result,null);assert.equal(requests.length,1);
});

test('a login completed after account context changed immediately revokes its minted native session',async()=>{
  let active=true;const {api,requests}=transport(()=>{active=false;});const revoked:string[]=[];
  const result=await authenticateNativeGoogle({api,apiBase,clientId,platform:'ios',proof,
    openBrowser:async()=>({type:'success',url:`milo://oauth?handoff=${handoff}`}),isActive:()=>active,revoke:async token=>{revoked.push(token);}});
  assert.equal(result,null);assert.equal(requests.length,2);assert.deepEqual(revoked,[session.access_token]);
});
