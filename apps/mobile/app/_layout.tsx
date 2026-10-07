import React,{useEffect,useRef} from 'react';
import {ActivityIndicator,KeyboardAvoidingView,Platform,View} from 'react-native';
import {Stack,Redirect,usePathname,useRouter,type Href} from 'expo-router';
import {StatusBar} from 'expo-status-bar';
import {useFonts} from 'expo-font';
import {DMSans_400Regular,DMSans_500Medium} from '@expo-google-fonts/dm-sans';
import {SpaceGrotesk_700Bold} from '@expo-google-fonts/space-grotesk';
import {SafeAreaProvider} from 'react-native-safe-area-context';
import {tokens} from '@milo/contracts';
import {AppProvider,useMilo} from '../lib/state';
import {Body,Button,Card,Heading,Screen} from '../components/ui';

function Routes(){const app=useMilo();const path=usePathname();const router=useRouter();const intended=useRef<string|null>(null);
  useEffect(()=>{if(app.mode==='signed-out'&&/^\/detail\/[a-z]+\/[A-Za-z0-9_-]+$/.test(path))intended.current=path;
    if(app.mode!=='signed-out'&&intended.current){const target=intended.current;intended.current=null;router.replace(target as Href);}},[app.mode,path,router]);
  if(app.recovering)return <View style={{flex:1,alignItems:'center',justifyContent:'center',backgroundColor:tokens.colors.canvas}}><ActivityIndicator accessibilityLabel="Restoring protected application session"/></View>;
  if(app.mode==='signed-out'&&path!=='/sign-in')return <Redirect href="/sign-in"/>;
  if(app.mode==='live'&&!app.snapshot)return <Screen title="Recovering current scope" hideDock><Card><Heading>Your private view is hidden</Heading><Body>Fetch a fresh authorized snapshot after your application session changes.</Body><Button label="Refresh current scope" onPress={()=>void app.refresh()}/><Button secondary label="Sign out of this device" onPress={()=>void app.logout()}/></Card></Screen>;
  return <KeyboardAvoidingView style={{flex:1}} behavior={Platform.OS==='ios'?'padding':undefined}><StatusBar style="dark"/>
    <Stack screenOptions={{headerShown:false,contentStyle:{backgroundColor:tokens.colors.canvas}}}>
      <Stack.Screen name="(tabs)"/><Stack.Screen name="sign-in"/>
      <Stack.Screen name="assistant" options={{presentation:'modal'}}/>
      <Stack.Screen name="detail/[kind]/[id]"/><Stack.Screen name="tools/[tool]"/>
    </Stack></KeyboardAvoidingView>;
}
export default function Layout(){const [loaded,error]=useFonts({DMSans_400Regular,DMSans_500Medium,SpaceGrotesk_700Bold});
  if(!loaded&&!error)return <View style={{flex:1,backgroundColor:tokens.colors.canvas,alignItems:'center',justifyContent:'center'}}><ActivityIndicator accessibilityLabel="Loading Milo typography"/></View>;
  return <SafeAreaProvider><AppProvider><Routes/></AppProvider></SafeAreaProvider>;}
