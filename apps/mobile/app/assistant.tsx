import React,{useState} from 'react';
import {View} from 'react-native';
import {useLocalSearchParams,useRouter,type Href} from 'expo-router';
import {routeObjectId,textValue,type RecordEntity,type AssistantState} from '@milo/contracts';
import {Badge,Body,Button,Card,Field,Heading,MiloFace,Screen,styles} from '../components/ui';
import {useMilo} from '../lib/state';
import {usePrivateResult} from '../lib/use-private-result';
import {reconcilePrivateResponse} from '../lib/private-results';
export default function Assistant(){const params=useLocalSearchParams<{conversation_id?:string}>();const app=useMilo();const data=app.snapshot;const router=useRouter();
  const [contextId,setContextId]=useState(routeObjectId(params.conversation_id));const [text,setText]=useState('');const [status,setStatus]=useState<AssistantState>('Idle');const [result,setResult]=usePrivateResult<unknown>(data);const [error,setError]=useState<string|null>(null);
  if(!data)return null;const chat=contextId?data.conversations.find(row=>row.id===contextId):null;
  const execute=async(command:string)=>{setStatus('Processing');setError(null);try {
    if(contextId&&!chat)throw new Error('This exact conversation is unavailable. Choose Home explicitly or another permitted conversation.');
    if(!data.workspace.id)throw new Error('Create your workspace before asking Milo to use account context.');
    if(!app.online)throw new Error('Refresh current authorized state before asking Milo to process this request.');
    if((command==='write_with_me'||command==='teach_me')&&!chat){setStatus('Needs information');setError('Choose an exact available conversation. Home context has no recipient.');return;}
    let sourceIds:string[]=[];if(command==='teach_me'){const messages=await app.request<RecordEntity[]>(`/v1/conversations/${chat!.id}/messages?limit=20&derived_evidence=true`);if(!messages.length)throw new Error('Teaching needs available evidence in this exact chat');sourceIds=[messages[messages.length-1].id];}
    const response=await app.request<{result:RecordEntity}>('/v1/assistant/commands',{method:'POST',body:{command,workspace_id:data.workspace.id,
      ...(chat?{conversation_id:chat.id}:{}),...(command==='write_with_me'?{instruction:text}:{}),...(command==='teach_me'?{text,status:'confirmed',source_message_ids:sourceIds}:{})}});
    const fresh=await app.refresh();if(!fresh)throw new Error('Current scope was not confirmed. Refresh to review the operation receipt.');
    const current=reconcilePrivateResponse(response.result,data,fresh);setResult(current,fresh);
    if(!current)throw new Error('Authorized context changed. The previous private result was cleared; request current context again.');
    setStatus(command==='write_with_me'?'Draft/proposal':'Done/receipt');
  }catch(error){setError(error instanceof Error?error.message:'Assistant unavailable');setStatus('Unavailable');}};
  return <Screen title="Ask Milo" hideDock><View style={styles.row}><MiloFace/><Badge status={status}/></View><Card><Heading>{chat?`${chat.title} context`:contextId?'Unavailable conversation context':'Home context'}</Heading><Body>{chat?`Exact account ${chat.connector_id} · recipient ${chat.provider_chat_id}`:'No conversation or recipient is selected. Typed prompts cannot create a route or broaden authority.'}</Body><Button secondary label="Use Home context" disabled={status==='Processing'} onPress={()=>{setContextId(null);setText('');setResult(null);setStatus('Idle');}}/>
    {data.conversations.map(row=><Button key={row.id} secondary label={`Use ${row.title} context`} disabled={contextId===row.id||status==='Processing'} onPress={()=>{setContextId(row.id);setText('');setResult(null);setStatus('Idle');}}/>)}</Card>
    <Card><Field label="Ask Milo · typed request" value={text} onChangeText={setText} multiline/><Body small>Choose the structured intent below. Text is not an unrestricted command or an approval to send.</Body>
      <Button label="Catch me up" disabled={status==='Processing'} onPress={()=>void execute('catch_me_up')}/><Button secondary label="Write with me · draft only" disabled={status==='Processing'||!chat} onPress={()=>void execute('write_with_me')}/><Button secondary label="Teach this exact chat" disabled={status==='Processing'||!chat||!text.trim()} onPress={()=>void execute('teach_me')}/>
      <Button label="Talk · transcription unavailable" disabled onPress={()=>{}}/><Body small>Microphone capture is not active. No always-listening input, call recording, automatic playback or voice authentication is implemented.</Body></Card>
    <View style={styles.row}><Button secondary label="Create a reminder" onPress={()=>router.push('/tools/reminder' as Href)}/><Button secondary label="Schedule allowed message" onPress={()=>router.push('/tools/schedule' as Href)}/><Button secondary label="Pause or Resume" onPress={()=>router.push('/tools/pause' as Href)}/></View>
    {result!==null&&<Card><Heading>{status==='Draft/proposal'?'Unsent proposal':'Structured result'}</Heading><Body>{JSON.stringify(result,null,2)}</Body>{status==='Draft/proposal'&&typeof result==='object'&&result&&'id' in result&&<Button label="Open exact draft review" onPress={()=>router.push(`/detail/draft/${textValue((result as RecordEntity).id)}` as Href)}/>}</Card>}
    {error&&<Card><Body>{error}</Body></Card>}<Button label="Close Milo and return to my page" secondary onPress={()=>router.canGoBack()?router.back():router.replace('/')}/>
  </Screen>;
}
