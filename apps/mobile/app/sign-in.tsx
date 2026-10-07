import React,{useEffect,useRef,useState} from 'react';
import {Platform,View} from 'react-native';
import * as AuthSession from 'expo-auth-session';
import * as WebBrowser from 'expo-web-browser';
import {useRouter} from 'expo-router';
import {nativeClient} from '../lib/native-api';
import {Body,Button,Card,Heading,MiloFace,Screen} from '../components/ui';
import {apiOrigin,useMilo} from '../lib/state';
import {authenticateNativeGoogle,NATIVE_APP_REDIRECT,nativeGoogleReady,type NativeGoogleConfig} from '../lib/native-oauth';
WebBrowser.maybeCompleteAuthSession();
type Config={google:NativeGoogleConfig};
export default function SignIn(){const app=useMilo();const router=useRouter();const [config,setConfig]=useState<Config|null>(null);const [busy,setBusy]=useState(false);const [error,setError]=useState<string|null>(null);
  const [checking,setChecking]=useState(true);const [attempt,setAttempt]=useState(0);
  const active=useRef(true);
  const loginFlight=useRef(false);
  useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
  useEffect(()=>{
    if(!apiOrigin){setChecking(false);setError('This app needs a configured sign-in service. You can explore the demo while setup is completed.');return;}
    let valid=true;const abort=new AbortController();let timedOut=false;
    setChecking(true);setConfig(null);setError(null);
    const timer=setTimeout(()=>{timedOut=true;abort.abort();},15000);
    nativeClient(apiOrigin).request<Config>('/v1/auth/config',{signal:abort.signal})
      .then(value=>{if(valid&&!abort.signal.aborted)setConfig(value);})
      .catch(()=>{if(valid)setError(timedOut?'Checking sign-in timed out. Check your connection and try again.':'The sign-in service is temporarily unavailable. Check your connection and try again.');})
      .finally(()=>{clearTimeout(timer);if(valid)setChecking(false);});
    return()=>{valid=false;clearTimeout(timer);abort.abort();};
  },[attempt]);
  const ready=nativeGoogleReady(config?.google,Platform.OS);
  const login=async()=>{if(!apiOrigin||!config||!ready||loginFlight.current)return;loginFlight.current=true;setBusy(true);setError(null);
    try {
      // AuthSession generates the local S256 proof only. Google's registered
      // HTTPS callback and code/token exchange are handled by the backend.
      const oauth=new AuthSession.AuthRequest({clientId:'milo-native-handoff',redirectUri:NATIVE_APP_REDIRECT,
        responseType:AuthSession.ResponseType.Code,scopes:['openid'],usePKCE:true});
      await oauth.getAuthRequestConfigAsync();
      if(!oauth.codeVerifier||!oauth.codeChallenge)throw new Error('Secure sign-in challenge generation failed');
      const session=await authenticateNativeGoogle({api:nativeClient(apiOrigin),apiBase:apiOrigin,clientId:config.google.client_id!,
        platform:Platform.OS==='ios'?'ios':'android',proof:{codeVerifier:oauth.codeVerifier,codeChallenge:oauth.codeChallenge},
        openBrowser:(url,redirect)=>WebBrowser.openAuthSessionAsync(url,redirect),isActive:()=>active.current,
        revoke:token=>nativeClient(apiOrigin!,token).request('/v1/auth/native/logout',{method:'POST'})});
      if(!session)return;
      await app.login(session);if(active.current)router.replace('/');
    } catch(error){if(active.current)setError(error instanceof Error?error.message:'Sign-in unavailable');}finally{loginFlight.current=false;if(active.current)setBusy(false);}
  };
  return <Screen title="Meet Milo" hideDock><View style={{alignItems:'flex-start',gap:16}}><MiloFace/><Heading>A little space for your busy day.</Heading>
    <Body>One place for your conversations, useful context, and the work you allow Milo to handle.</Body></View>
    <Card><Heading>Your account, your boundaries</Heading><Body>Google identifies you. Connecting Gmail, Calendar, WhatsApp, or device contacts requires separate permission.</Body>
      <Button label={busy?'Signing in…':checking?'Checking sign-in…':'Continue with Google'} disabled={busy||checking||!ready} onPress={()=>void login()}/>
      {!ready&&!checking&&<Body small>{Platform.OS==='web'?'Use the Milo website for browser Google sign-in.':config?'Google sign-in is not available yet. You can explore the demo while setup is completed.':'Sign-in availability could not be confirmed.'}</Body>}
      {error&&<View accessibilityLiveRegion="assertive"><Body>{error}</Body></View>}
      {!checking&&apiOrigin&&<Button label={config?'Refresh sign-in availability':'Retry sign-in check'} secondary disabled={busy} onPress={()=>setAttempt(value=>value+1)}/>}</Card>
    <Card><Heading>Explore the complete native experience</Heading><Body>A labelled synthetic demo. It connects no account and sends no messages.</Body>
      <Button label="Open synthetic demo" secondary disabled={busy} onPress={()=>{app.startDemo();router.replace('/');}}/></Card>
    <Body small>App sessions are separate from WhatsApp connection sessions. Closing Milo does not stop authorized server Auto.</Body>
  </Screen>;
}
