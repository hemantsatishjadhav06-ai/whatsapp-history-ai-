import React,{useState} from 'react';
import {View} from 'react-native';
import {useRouter,type Href} from 'expo-router';
import {textValue} from '@milo/contracts';
import {Badge,Body,Button,Card,Field,Heading,Screen,styles} from '../../components/ui';
import {useMilo} from '../../lib/state';
export default function Memory(){const data=useMilo().snapshot;const router=useRouter();const [query,setQuery]=useState('');const [scope,setScope]=useState('all');const [status,setStatus]=useState('all');if(!data)return null;
  const memories=data.memories.filter(row=>textValue(row.text).toLowerCase().includes(query.toLowerCase())&&(scope==='all'||row.conversation_id===scope)&&(status==='all'||row.status===status));
  return <Screen title="Memory"><Button label="People & voice" onPress={()=>router.push('/tools/people' as Href)}/><Button secondary label="Teach a scoped preference" onPress={()=>router.push('/tools/teach' as Href)}/><Button secondary label="Commitments & reminders" onPress={()=>router.push('/actions' as Href)}/>
    <Heading>Facts & preferences</Heading><Field label="Find permitted memory" value={query} onChangeText={setQuery}/><View style={styles.row}>{['all','confirmed','proposed'].map(value=><Button key={value} secondary label={value==='all'?'All statuses':value} disabled={status===value} onPress={()=>setStatus(value)}/>)}</View>
    <Card><Heading>Conversation scope</Heading><Button secondary label="All permitted scopes" disabled={scope==='all'} onPress={()=>setScope('all')}/>{data.conversations.map(chat=><Button key={chat.id} secondary label={`${chat.title} · ${chat.kind}`} disabled={scope===chat.id} onPress={()=>setScope(chat.id)}/>)}</Card>
    {memories.map(row=><Card key={row.id}><Badge status={textValue(row.status)}/><Body>{textValue(row.text)}</Body><Body small>{data.conversations.find(chat=>chat.id===row.conversation_id)?.title??'Selected scope'} · Source version {textValue(String(row.version??1))}</Body><Button secondary label="Open evidence, correct or forget" onPress={()=>router.push(`/detail/memory/${row.id}` as Href)}/></Card>)}
    {!memories.length&&<Card><Heading>No matching available memory</Heading><Body>Memory needs selected evidence and owner confirmation. Group and direct-chat scope remain separate.</Body></Card>}
  </Screen>;
}
