import React from 'react';
import {useRouter,type Href} from 'expo-router';
import {actionLabel,formatDate,textValue} from '@milo/contracts';
import {Badge,Body,Button,Card,Heading,Screen} from '../../components/ui';
import {useMilo} from '../../lib/state';
export default function Actions(){const data=useMilo().snapshot;const router=useRouter();if(!data)return null;
  return <Screen title="Actions"><Button label="Create an owner reminder" onPress={()=>router.push('/tools/reminder' as Href)}/><Button secondary label="Schedule an allowed message" onPress={()=>router.push('/tools/schedule' as Href)}/>
    <Heading>Communication receipts</Heading>{data.actions.length?data.actions.map(row=><Card key={row.id}><Badge status={textValue(row.status)}/><Heading>{actionLabel(textValue(row.kind))}</Heading><Body small>{data.conversations.find(chat=>chat.id===row.destination_conversation_id)?.title??'Selected recipient'} · {textValue(row.reason_code)}</Body><Button secondary label="Open receipt and evidence" onPress={()=>router.push(`/detail/action/${row.id}` as Href)}/></Card>):<Body>No outbound actions in this authorized view.</Body>}
    <Heading>Owner reminders</Heading>{data.tasks.map(row=><Card key={row.id}><Heading>{textValue(row.title)}</Heading><Body>{formatDate(row.due_at,data.workspace.timezone)}</Body><Badge status={textValue(row.status)}/><Button label="Open reminder" secondary onPress={()=>router.push(`/detail/task/${row.id}` as Href)}/></Card>)}
    <Heading>Durable external schedules</Heading>{data.jobs.map(row=><Card key={row.id}><Heading>{textValue(row.purpose,'Scheduled message')}</Heading><Badge status={textValue(row.status)}/><Button secondary label="Open schedule" onPress={()=>router.push(`/detail/job/${row.id}` as Href)}/></Card>)}{!data.jobs.length&&<Body>No external schedules. A reminder is separate from an external message.</Body>}
  </Screen>;
}
