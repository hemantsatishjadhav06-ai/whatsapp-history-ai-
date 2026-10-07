import {useEffect,useState} from 'react';
import {currentPrivateResult,snapshotVersion,type AuthorizedSnapshot,type PrivateResult} from './private-results';

export function usePrivateResult<T>(snapshot:AuthorizedSnapshot|null):[T|null,(value:T|null,authorized?:AuthorizedSnapshot|null)=>void] {
  const [entry,setEntry]=useState<PrivateResult<T>|null>(null);
  const version=snapshotVersion(snapshot);
  useEffect(()=>{setEntry(previous=>previous&&previous.version!==version?null:previous);},[version]);
  return [currentPrivateResult(entry,snapshot),(value,authorized=snapshot)=>setEntry(value===null?null:{value,version:snapshotVersion(authorized)})];
}
