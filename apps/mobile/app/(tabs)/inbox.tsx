import React,{useState} from 'react';
import {View} from 'react-native';
import {useRouter,type Href} from 'expo-router';
import {modeFor,stateLabel} from '@milo/contracts';
import {Badge,Body,Button,Card,Field,Heading,Screen,styles} from '../../components/ui';
import {useMilo} from '../../lib/state';
export default function Inbox(){const app=useMilo();const data=app.snapshot;const router=useRouter();const [query,setQuery]=useState('');const [filter,setFilter]=useState('all');const [account,setAccount]=useState('all');if(!data)return null;
  const chats=data.conversations.filter(row=>row.title.toLowerCase().includes(query.toLowerCase())&&(account==='all'||row.connector_id===account)&&(filter==='all'||row.kind===filter||modeFor(row,data.grants)===filter));
  return <Screen title="Inbox"><Field label="Find a selected conversation" value={query} onChangeText={setQuery}/><View style={styles.row}>{['all','contact','group','Auto','draft','read-only','disabled'].map(value=><Button key={value} label={value==='all'?'All':value} secondary disabled={filter===value} onPress={()=>setFilter(value)}/>)}</View>
    {data.connections.length>1&&<Card><Heading>Account</Heading><Button secondary label="All permitted accounts" disabled={account==='all'} onPress={()=>setAccount('all')}/>{data.connections.map(row=><Button key={row.id} secondary label={row.account_id} disabled={account===row.id} onPress={()=>setAccount(row.id)}/>)}</Card>}
    {!chats.length&&<Card><Heading>No available conversations</Heading><Body>Select read scope in Connections or change your filter.</Body><Button label="Open scope setup" onPress={()=>router.push('/tools/scope' as Href)}/></Card>}
    {chats.map(chat=><Card key={chat.id}><View style={styles.row}><Heading>{chat.title}</Heading><Badge status={chat.control_state}/></View><Body small>{chat.account_label??'Verified account'} · {chat.kind==='group'?'Group':'Direct'} · Saved mode {modeFor(chat,data.grants)}</Body><Body>{chat.preview??'No current preview available'}</Body>
      {chat.control_state==='HUMAN_TAKEOVER'&&<Body small>Phone takeover is sticky. Incoming messages do not resume this chat.</Body>}<Button label={`Open ${chat.title}`} secondary onPress={()=>router.push(`/detail/conversation/${chat.id}` as Href)}/></Card>)}
    {data.pagination?.has_more_conversations&&<Button label="Load more selected conversations" onPress={()=>void app.loadMore()}/>}</Screen>;
}
