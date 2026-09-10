import { FormEvent, useEffect, useState } from 'react'
import { FileText, Plus, Star, Trash2, UploadCloud } from 'lucide-react'
import { api } from '../api'
import { EmptyState, ErrorMessage, Loading, PageHeader } from '../components'
import type { Resume } from '../types'

export function ResumesPage() {
  const [resumes,setResumes]=useState<Resume[]>(); const [error,setError]=useState<unknown>(); const [show,setShow]=useState(false)
  const load=()=>api.get<Resume[]>('/resumes').then(setResumes).catch(setError); useEffect(()=>{void load()},[])
  const upload=async(e:FormEvent<HTMLFormElement>)=>{e.preventDefault();const form=e.currentTarget;const data=new FormData(form);try{await api.upload('/resumes',data);form.reset();setShow(false);load()}catch(err){setError(err)}}
  const makeDefault=async(id:string)=>{await api.patch(`/resumes/${id}`,{is_default:true});load()}; const remove=async(id:string)=>{if(confirm('Remove this resume from local storage?')){await api.delete(`/resumes/${id}`);load()}}
  if(!resumes&&!error)return <Loading/>
  return <><PageHeader eyebrow="Document library" title="Resumes" description="Store role-specific PDF resumes locally. Text is extracted on-device for matching." action={<button className="button primary" onClick={()=>setShow(!show)}><Plus size={16}/> Add resume</button>}/><ErrorMessage error={error}/>
    {show&&<form className="panel upload-panel" onSubmit={upload}><UploadCloud size={32}/><div><h2>Upload a PDF resume</h2><p>Maximum 10 MB. The original document and extracted text remain in your local data directory.</p></div><input name="file" type="file" accept="application/pdf" required/><input name="label" placeholder="Label, e.g. AI / ML" required/><input name="target_roles" placeholder="Target roles, comma separated"/><input name="skills" placeholder="Skills represented, comma separated"/><label className="check"><input type="checkbox" name="is_default" value="true"/> Set as default</label><button className="button primary" type="submit">Upload resume</button></form>}
    {resumes?.length?<div className="card-grid">{resumes.map(resume=><article className="panel resume-card" key={resume.id}><div className="file-illustration"><FileText size={30}/>{resume.is_default&&<span><Star size={12}/> Default</span>}</div><div><h2>{resume.label}</h2><p>{resume.filename}</p></div><div className="tag-row">{resume.target_roles.map(x=><span className="tag" key={x}>{x}</span>)}</div><small>Added {new Date(resume.created_at).toLocaleDateString()}</small><div className="card-actions">{!resume.is_default&&<button className="button secondary small" onClick={()=>makeDefault(resume.id)}><Star size={14}/> Make default</button>}<button className="icon-button danger" onClick={()=>remove(resume.id)}><Trash2 size={16}/></button></div></article>)}</div>:<EmptyState title="No resumes yet">Add your first PDF. One resume can be marked as the default for new analyses and applications.</EmptyState>}
  </>
}
