import React from 'react';
import {ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View, type TextInputProps} from 'react-native';
import {tokens,stateLabel} from '@milo/contracts';
import {SafeAreaView,useSafeAreaInsets} from 'react-native-safe-area-context';
import {useMilo} from '../lib/state';
import {useRouter,type Href} from 'expo-router';
const c=tokens.colors;
export const styles=StyleSheet.create({page:{flex:1,backgroundColor:c.canvas},content:{padding:20,paddingBottom:120,gap:16},
  title:{fontFamily:tokens.fonts.heading,fontSize:28,color:c.ink},heading:{fontFamily:tokens.fonts.heading,fontSize:20,color:c.ink},
  text:{fontFamily:tokens.fonts.body,fontSize:16,lineHeight:24,color:c.ink},small:{fontFamily:tokens.fonts.body,fontSize:13,lineHeight:20,color:c.secondary},
  card:{backgroundColor:c.surface,borderColor:c.border,borderWidth:1,borderRadius:24,padding:20,gap:10},row:{flexDirection:'row',alignItems:'center',gap:12,flexWrap:'wrap'},
  button:{minHeight:48,minWidth:48,borderRadius:16,backgroundColor:c.primary,paddingHorizontal:20,paddingVertical:13,alignItems:'center',justifyContent:'center'},
  buttonText:{fontFamily:tokens.fonts.medium,fontSize:16,color:c.primaryText},field:{minHeight:52,borderWidth:1,borderColor:c.border,borderRadius:16,backgroundColor:c.surface,padding:14,fontFamily:tokens.fonts.body,fontSize:16,color:c.ink},
  badge:{alignSelf:'flex-start',borderRadius:99,paddingHorizontal:12,paddingVertical:6,backgroundColor:c.selected},banner:{padding:14,borderRadius:16,backgroundColor:c.caution}});
export function Heading({children}:{children:React.ReactNode}){return <Text accessibilityRole="header" style={styles.heading}>{children}</Text>;}
export function Body({children,small=false}:{children:React.ReactNode;small?:boolean}){return <Text style={small?styles.small:styles.text}>{children}</Text>;}
export function Card({children}:{children:React.ReactNode}){return <View style={styles.card}>{children}</View>;}
export function Warning({children}:{children:React.ReactNode}){return <View accessible accessibilityRole="alert" style={styles.banner}><Text style={styles.text}>{children}</Text></View>;}
export function Button({label,onPress,secondary=false,disabled=false}:{label:string;onPress:()=>void;secondary?:boolean;disabled?:boolean}){
  return <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{disabled}} disabled={disabled}
    style={[styles.button,secondary&&{backgroundColor:c.selected},disabled&&{opacity:0.55}]} onPress={onPress}>
    <Text style={[styles.buttonText,secondary&&{color:c.primary}]}>{label}</Text></Pressable>;
}
export function Field({label,...props}:TextInputProps&{label:string}){return <View style={{gap:6}}><Body small>{label}</Body>
  <TextInput {...props} accessibilityLabel={label} style={[styles.field,props.multiline&&{minHeight:120,textAlignVertical:'top'},props.style]} placeholderTextColor={c.secondary} maxLength={props.maxLength??2000}/></View>;}
export function Badge({status}:{status:string}){return <View style={styles.badge}><Text style={styles.small}>{stateLabel(status)}</Text></View>;}
export function MiloFace(){return <View accessible accessibilityLabel="Milo companion" style={{width:50,height:48,borderRadius:22,backgroundColor:c.peach,alignItems:'center',justifyContent:'center'}}>
  <Text style={{fontSize:26,color:c.ink}}>•ᴗ•</Text></View>;}
export function Screen({title,children,contextId,hideDock=false}:{title:string;children:React.ReactNode;contextId?:string;hideDock?:boolean}){
  const app=useMilo();const router=useRouter();const insets=useSafeAreaInsets();
  return <SafeAreaView style={styles.page} edges={['top','left','right']}><ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.content}>
    <View style={[styles.row,{justifyContent:'space-between'}]}><Text accessibilityRole="header" style={styles.title}>{title}</Text>
      {app.snapshot?.workspace.id&&<Button label={app.snapshot.workspace.paused?'Resume':'Pause'} secondary onPress={()=>router.push('/tools/pause' as Href)}/>}</View>
    {(app.mode==='demo'||app.snapshot?.simulation)&&<View style={styles.banner}><Body small>Synthetic demo · no accounts connected or messages sent</Body></View>}
    {app.pausePending&&<View accessibilityLiveRegion="assertive" style={styles.banner}><Body>Pause not confirmed; automation may still be active</Body>
      <Button label="Retry exact control request" onPress={()=>void app.pause(app.pendingPauseTarget??true).catch(()=>{})}/></View>}
    {!app.online&&<View style={styles.banner}><Body>Offline or unavailable · cached view from {app.lastUpdated??'unknown time'}. Server Auto may still be active.</Body><Button secondary label="Refresh authorized state" onPress={()=>void app.refresh()}/></View>}
    {app.error&&<Body small>{app.error}</Body>}
    {app.refreshing&&<ActivityIndicator accessibilityLabel="Refreshing current server state" color={c.primary}/>}
    {children}
  </ScrollView>{!hideDock&&app.snapshot&&<Pressable accessibilityRole="button" accessibilityLabel={contextId?'Ask Milo about this conversation':'Ask Milo in Home context'}
    style={{position:'absolute',bottom:Math.max(12,insets.bottom),left:20,right:20,minHeight:52,borderRadius:24,backgroundColor:c.selected,padding:14,flexDirection:'row',alignItems:'center',gap:12}}
    onPress={()=>router.push({pathname:'/assistant',params:contextId?{conversation_id:contextId}:{}} as Href)}><MiloFace/><View style={{flex:1}}><Body>Ask Milo</Body><Body small>{contextId?'Current conversation':'Home context · no recipient selected'}</Body></View></Pressable>}</SafeAreaView>;
}
