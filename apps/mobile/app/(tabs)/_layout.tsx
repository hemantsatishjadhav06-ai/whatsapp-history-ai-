import React from 'react';
import {Text} from 'react-native';
import {Tabs} from 'expo-router';
import {tokens} from '@milo/contracts';
export default function Layout(){return <Tabs screenOptions={{headerShown:false,tabBarActiveTintColor:tokens.colors.primary,
  tabBarInactiveTintColor:tokens.colors.secondary,tabBarStyle:{backgroundColor:tokens.colors.surface,borderTopColor:tokens.colors.border,minHeight:64},
  tabBarLabelStyle:{fontFamily:tokens.fonts.medium,fontSize:12},tabBarItemStyle:{minHeight:48}}}>
  {([['index','Home','⌂'],['inbox','Inbox','▤'],['actions','Actions','✓'],['memory','Memory','◇'],['more','More','•••']] as const).map(([name,title,icon])=>
    <Tabs.Screen key={name} name={name} options={{title,tabBarAccessibilityLabel:title,tabBarIcon:({color})=><Text style={{color,fontSize:22}}>{icon}</Text>}}/>)}
</Tabs>;}
