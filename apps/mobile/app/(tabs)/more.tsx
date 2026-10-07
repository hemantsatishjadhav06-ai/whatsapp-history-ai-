import React from 'react';
import {useRouter,type Href} from 'expo-router';
import {Body,Button,Card,Heading,Screen} from '../../components/ui';
const tools=[['rules','Rules'],['connections','Connections'],['activity','Activity'],['settings','Settings'],['privacy','Privacy'],['devices','Security & devices'],['usage','Usage & quota'],['notifications','Notifications & focus'],['system-access','System access'],['onboarding','Owner setup']] as const;
export default function More(){const router=useRouter();return <Screen title="More"><Card><Heading>Your tools, in one place</Heading><Body>Connections, memory, Rules and privacy use the same account and scoped server authority.</Body></Card>{tools.map(([tool,label])=><Button key={tool} label={label} secondary onPress={()=>router.push(`/tools/${tool}` as Href)}/>)}</Screen>;}
