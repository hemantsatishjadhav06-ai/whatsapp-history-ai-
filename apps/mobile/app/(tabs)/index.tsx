import React from 'react';
import {View} from 'react-native';
import {useRouter,type Href} from 'expo-router';
import {actionLabel,formatDate,stateLabel,textValue} from '@milo/contracts';
import {Badge,Body,Button,Card,Heading,MiloFace,Screen,styles} from '../../components/ui';
import {useMilo} from '../../lib/state';
export default function Home(){const app=useMilo();const data=app.snapshot;const router=useRouter();if(!data)return null;
  const needs=data.actions.filter(row=>['blocked','uncertain','needs_approval'].includes(textValue(row.status))).slice(0,5);
  const handled=data.actions.filter(row=>['accepted','delivered','read'].includes(textValue(row.status)));
  const needsConnection=app.mode==='live'&&!!data.workspace.id&&!data.conversations.length;
  return <Screen title="Home"><View style={styles.row}><MiloFace/><View style={{flex:1}}><Heading>Hello, {data.user.display_name}</Heading>
    <Body small>{!data.workspace.id?'Create your workspace to begin':data.workspace.paused?'External replies & actions paused':'Your conversations, with room to breathe.'}</Body></View></View>
    {!data.workspace.id&&<Card><Heading>Set up your space</Heading><Body>Choose your timezone, connect an eligible account, and select exact chats before enabling Auto.</Body><Button label="Start owner setup" onPress={()=>router.push('/tools/onboarding' as Href)}/></Card>}
    {needsConnection&&<Card><Heading>Bring in your first conversation</Heading><Body>Link your WhatsApp phone when the pilot is available, connect an eligible Business number, or import a selected chat. You choose what Milo may read and learn.</Body><Button label="Continue setup" onPress={()=>router.push('/tools/onboarding' as Href)}/><Body small>No conversations are being monitored yet. Connecting alone enables no automatic replies.</Body></Card>}
    <Card><Heading>Needs you</Heading>{needs.length?needs.map(row=><View key={row.id} style={{gap:8}}><Badge status={textValue(row.status)}/><Body>{actionLabel(textValue(row.kind))} · {data.conversations.find(chat=>chat.id===row.conversation_id)?.title??'Selected conversation'}</Body>
      <Body small>{textValue(row.reason_code,'Review the available evidence')}</Body><Button secondary label="Open current action" onPress={()=>router.push(`/detail/action/${row.id}` as Href)}/></View>):<Body>{needsConnection?'Decisions appear after you select your first chat.':'No pending decisions in this view.'}</Body>}</Card>
    <Card><Heading>A useful next step</Heading><Button label="Catch me up" onPress={()=>router.push('/tools/catch-up' as Href)}/><Button secondary label="Write with me" onPress={()=>router.push('/assistant' as Href)}/><Button secondary label="Teach Milo a preference" onPress={()=>router.push('/tools/teach' as Href)}/></Card>
    <Card><Heading>Upcoming</Heading>{data.tasks.filter(row=>row.status==='pending').slice(0,3).map(row=><View key={row.id}><Body>{textValue(row.title)}</Body><Body small>Owner reminder · {formatDate(row.due_at,data.workspace.timezone)}</Body><Button secondary label="Open reminder" onPress={()=>router.push(`/detail/task/${row.id}` as Href)}/></View>)}{!data.tasks.length&&<Body>No owner reminders yet.</Body>}</Card>
    <Card><Heading>Handled</Heading><Body>{handled.length} evidenced receipts in this view</Body>{handled.slice(0,3).map(row=><Body key={row.id}>{actionLabel(textValue(row.kind))} · {stateLabel(textValue(row.status))}</Body>)}<Body small>Provider acceptance and delivery are separate. Reading this card completes no action.</Body></Card>
    <Body small>Source freshness: {app.lastUpdated?formatDate(app.lastUpdated,data.workspace.timezone):'Not refreshed'}. History completeness remains unknown unless the provider proves it.</Body>
  </Screen>;
}
