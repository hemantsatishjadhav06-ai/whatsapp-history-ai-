import React,{useEffect,useRef,useState} from 'react';
import {Platform,View} from 'react-native';
import * as AuthSession from 'expo-auth-session';
import * as WebBrowser from 'expo-web-browser';
import {useRouter} from 'expo-router';
import {nativeClient} from '../lib/native-api';
import {Body,Button,Card,Heading,MiloFace,Screen} from '../components/ui';
import {apiOrigin,useMilo} from '../lib/state';
import type {StoredSession} from '../lib/security';
WebBrowser.maybeCompleteAuthSession();
const discovery={authorizationEndpoint:'https://accounts.google.com/o/oauth2/v2/auth',tokenEndpoint:'https://oauth2.googleapis.com/token'};
type Config={google:{configured:boolean;native_configured:boolean;client_id:string;android_client_id:string;ios_client_id:string}};
export default function SignIn(){const app=useMilo();const router=useRouter();const [config,setConfig]=useState<Config|null>(null);const [busy,setBusy]=useState(false);const [error,setError]=useState<string|null>(null);
  const active=useRef(true);
  useEffect(()=>()=>{active.current=false;},[]);
  useEffect(()=>{if(!apiOrigin)return;const abort=new AbortController();nativeClient(apiOrigin).request<Config>('/v1/auth/config',{signal:abort.signal})
    .then(setConfig).catch(()=>setError('The configured sign-in service is unavailable')).finally(()=>{});return()=>abort.abort();},[]);
  const login=async()=>{if(!apiOrigin||!config)return;setBusy(true);setError(null);
    try {const clientId=Platform.OS==='ios'?config.google.ios_client_id:config.google.android_client_id;
      if(!clientId)throw new Error('An installed-app Google client ID and registered redirect are required');
      const redirectUri=AuthSession.makeRedirectUri({scheme:'milo',path:'oauth'});
      const oauth=new AuthSession.AuthRequest({clientId,redirectUri,responseType:AuthSession.ResponseType.Code,scopes:['openid','email','profile'],usePKCE:true});
      await oauth.makeAuthUrlAsync(discovery);
      if(!oauth.codeVerifier||!oauth.codeChallenge)throw new Error('Secure sign-in challenge generation failed');
      const publicApi=nativeClient(apiOrigin);
      const challenge=await publicApi.request<{nonce:string;expires_at:string}>('/v1/auth/native/nonce',{method:'POST',
        body:{platform:Platform.OS==='ios'?'ios':'android',device_name:`Milo ${Platform.OS}`,code_challenge:oauth.codeChallenge}});
      oauth.extraParams.nonce=challenge.nonce;await oauth.makeAuthUrlAsync(discovery);
      const response=await oauth.promptAsync(discovery);
      if(response.type==='cancel'||response.type==='dismiss'){setError('Sign-in canceled. You can try again.');return;}
      if(response.type!=='success'||!response.params.code)throw new Error('Google did not return a valid sign-in authorization');
      const token=await AuthSession.exchangeCodeAsync({clientId,code:response.params.code,redirectUri,
        extraParams:{code_verifier:oauth.codeVerifier}},discovery);
      if(!token.idToken)throw new Error('Google did not return a verified identity token');
      const session=await publicApi.request<Omit<StoredSession,'origin'|'environment'>>('/v1/auth/native/login',{method:'POST',
        body:{credential:token.idToken,nonce:challenge.nonce,code_verifier:oauth.codeVerifier}});
      if(!active.current){await nativeClient(apiOrigin,session.access_token).request('/v1/auth/native/logout',{method:'POST'}).catch(()=>{});return;}
      await app.login(session);if(active.current)router.replace('/');
    } catch(error){if(active.current)setError(error instanceof Error?error.message:'Sign-in unavailable');}finally{if(active.current)setBusy(false);}
  };
  return <Screen title="Meet Milo" hideDock><View style={{alignItems:'flex-start',gap:16}}><MiloFace/><Heading>A little space for your busy day.</Heading>
    <Body>One place for your conversations, useful context, and the work you allow Milo to handle.</Body></View>
    <Card><Heading>Your account, your boundaries</Heading><Body>Google identifies you. Connecting Gmail, Calendar, WhatsApp, or device contacts requires separate permission.</Body>
      <Button label={busy?'Signing in…':'Continue with Google'} disabled={busy||!config?.google.native_configured} onPress={()=>void login()}/>
      {!config?.google.native_configured&&<Body small>Native Google sign-in is unavailable until the API origin, installed-app client IDs, and redirect are configured.</Body>}
      {error&&<Body>{error}</Body>}</Card>
    <Card><Heading>Explore the complete native experience</Heading><Body>A labelled synthetic demo. It connects no account and sends no messages.</Body>
      <Button label="Open synthetic demo" secondary onPress={()=>{app.startDemo();router.replace('/');}}/></Card>
    <Body small>App sessions are separate from WhatsApp connection sessions. Closing Milo does not stop authorized server Auto.</Body>
  </Screen>;
}
