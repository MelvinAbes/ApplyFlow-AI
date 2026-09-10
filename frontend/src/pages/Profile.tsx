import { useEffect, useMemo, useState } from 'react'
import { Plus, Save, Trash2 } from 'lucide-react'
import { api } from '../api'
import { ErrorMessage, Field, Loading, PageHeader } from '../components'
import type { CandidateProfile } from '../types'

const emptyProfile: CandidateProfile = {
  skills: [], programming_languages: [], frameworks: [], tools: [], preferred_job_types: [], preferred_locations: [],
  preferred_technologies: [], undesired_roles: [], seniority: [], minimum_match_score: 60,
  educations: [], work_experiences: [], languages: [],
}
const listFields = ['skills', 'programming_languages', 'frameworks', 'tools', 'preferred_job_types', 'preferred_locations', 'preferred_technologies', 'undesired_roles', 'seniority'] as const
const text = (value: unknown) => typeof value === 'string' ? value : ''

export function ProfilePage() {
  const [profile, setProfile] = useState<CandidateProfile>()
  const [error, setError] = useState<unknown>()
  const [saved, setSaved] = useState(false)
  useEffect(() => { api.get<CandidateProfile | null>('/profile').then(data => setProfile(data || emptyProfile)).catch(setError) }, [])
  const completeness = useMemo(() => profile ? Math.round(100 * [profile.first_name, profile.last_name, profile.email, profile.phone, profile.city, profile.current_title, profile.educations.length, profile.skills.length, profile.linkedin].filter(Boolean).length / 9) : 0, [profile])
  if (!profile && !error) return <Loading/>
  const update = (key: keyof CandidateProfile, value: unknown) => setProfile(current => current ? { ...current, [key]: value } : current)
  const save = async () => { if (!profile) return; setError(undefined); setSaved(false); try { setProfile(await api.put<CandidateProfile>('/profile', profile)); setSaved(true) } catch (e) { setError(e) } }
  return <>
    <PageHeader eyebrow="Source of truth" title="Candidate profile" description="Only facts stored here, in a resume, or in your answer bank may be used in an application." action={<button className="button primary" onClick={save}><Save size={16}/> Save profile</button>}/>
    <ErrorMessage error={error}/>{saved && <div className="notice success">Profile saved. Cached job analyses will use revision {profile?.revision}.</div>}
    {profile && <div className="profile-layout"><aside className="panel profile-meter"><div className="meter-ring" style={{'--progress': `${completeness * 3.6}deg`} as React.CSSProperties}><strong>{completeness}%</strong></div><h3>Profile completeness</h3><p>A complete profile enables more deterministic answers and fewer AI calls.</p><div className="privacy-rule"><strong>Truthfulness rule</strong><span>Blank values stay blank. The assistant will never guess them.</span></div></aside>
      <div className="form-stack">
        <ProfileSection title="Personal details" description="Contact and location details commonly requested by application forms.">
          <div className="form-grid">{[
            ['first_name','First name'],['last_name','Last name'],['preferred_name','Preferred name'],['email','Email'],['phone','Phone'],['city','City'],['country','Country'],['postal_code','Postal code'],['street_name','Street name'],['house_number','House number'],['address','Combined address (fallback)'],['nationality','Nationality'],['date_of_birth','Date of birth'],['gender','Gender'],['honorific_title','Personal title'],['name_suffix','Name suffix'],
          ].map(([key,label]) => <Field label={label} key={key}><input type={key==='date_of_birth'?'date':'text'} value={text(profile[key as keyof CandidateProfile])} onChange={e=>update(key as keyof CandidateProfile,e.target.value)}/></Field>)}</div>
        </ProfileSection>
        <ProfileSection title="Professional profile" description="Use concise, factual terms. Comma-separated entries become structured lists.">
          <div className="form-grid"><Field label="Current title"><input value={profile.current_title || ''} onChange={e=>update('current_title',e.target.value)}/></Field>{listFields.slice(0,4).map(key => <Field label={key.replaceAll('_',' ')} key={key}><input value={profile[key].join(', ')} onChange={e=>update(key,e.target.value.split(',').map(x=>x.trim()).filter(Boolean))}/></Field>)}</div>
          <h3 className="subheading">Experience</h3>{profile.work_experiences.map((item,index)=><div className="repeat-row" key={index}><input placeholder="Role title" value={item.title} onChange={e=>update('work_experiences',profile.work_experiences.map((x,i)=>i===index?{...x,title:e.target.value}:x))}/><input placeholder="Company" value={item.company || ''} onChange={e=>update('work_experiences',profile.work_experiences.map((x,i)=>i===index?{...x,company:e.target.value}:x))}/><textarea placeholder="Factual description" value={item.description || ''} onChange={e=>update('work_experiences',profile.work_experiences.map((x,i)=>i===index?{...x,description:e.target.value}:x))}/><button className="icon-button danger" onClick={()=>update('work_experiences',profile.work_experiences.filter((_,i)=>i!==index))}><Trash2 size={16}/></button></div>)}<button className="button secondary small" onClick={()=>update('work_experiences',[...profile.work_experiences,{title:''}])}><Plus size={15}/> Add role</button>
        </ProfileSection>
        <ProfileSection title="Education & languages" description="Multiple entries are supported; mark the active degree as current.">
          {profile.educations.map((item,index)=><div className="repeat-row education" key={index}><input placeholder="Institution" value={item.institution} onChange={e=>update('educations',profile.educations.map((x,i)=>i===index?{...x,institution:e.target.value}:x))}/><input placeholder="Degree" value={item.degree || ''} onChange={e=>update('educations',profile.educations.map((x,i)=>i===index?{...x,degree:e.target.value}:x))}/><input placeholder="Field of study" value={item.field_of_study || ''} onChange={e=>update('educations',profile.educations.map((x,i)=>i===index?{...x,field_of_study:e.target.value}:x))}/><input type="date" title="Graduation date" value={item.graduation_date || ''} onChange={e=>update('educations',profile.educations.map((x,i)=>i===index?{...x,graduation_date:e.target.value}:x))}/><label className="check"><input type="checkbox" checked={item.current_student} onChange={e=>update('educations',profile.educations.map((x,i)=>i===index?{...x,current_student:e.target.checked}:x))}/> Current</label><button className="icon-button danger" onClick={()=>update('educations',profile.educations.filter((_,i)=>i!==index))}><Trash2 size={16}/></button></div>)}<button className="button secondary small" onClick={()=>update('educations',[...profile.educations,{institution:'',current_student:false}])}><Plus size={15}/> Add education</button>
          <div className="language-list">{profile.languages.map((item,index)=><div className="inline-fields" key={index}><input placeholder="Language" value={item.language} onChange={e=>update('languages',profile.languages.map((x,i)=>i===index?{...x,language:e.target.value}:x))}/><input placeholder="Proficiency (e.g. B2)" value={item.proficiency || ''} onChange={e=>update('languages',profile.languages.map((x,i)=>i===index?{...x,proficiency:e.target.value}:x))}/><button className="icon-button danger" onClick={()=>update('languages',profile.languages.filter((_,i)=>i!==index))}><Trash2 size={16}/></button></div>)}</div><button className="button secondary small" onClick={()=>update('languages',[...profile.languages,{language:''}])}><Plus size={15}/> Add language</button>
        </ProfileSection>
        <ProfileSection title="Links & preferences" description="Used for direct field mapping and relevance scoring.">
          <div className="form-grid">{[['linkedin','LinkedIn'],['github','GitHub'],['portfolio','Portfolio'],['personal_website','Website'],['remote_preference','Remote preference']].map(([key,label])=><Field label={label} key={key}><input value={text(profile[key as keyof CandidateProfile])} onChange={e=>update(key as keyof CandidateProfile,e.target.value)}/></Field>)}{listFields.slice(4).map(key=><Field label={key.replaceAll('_',' ')} key={key}><input value={profile[key].join(', ')} onChange={e=>update(key,e.target.value.split(',').map(x=>x.trim()).filter(Boolean))}/></Field>)}<Field label="Minimum match score"><input type="number" min="0" max="100" value={profile.minimum_match_score} onChange={e=>update('minimum_match_score',Number(e.target.value))}/></Field></div>
        </ProfileSection>
        <ProfileSection title="Application facts" description="Leave anything unknown blank. These values can answer repeated factual questions.">
          <div className="form-grid">{[['availability','Availability'],['preferred_start_date','Preferred start date'],['work_authorization','Work authorization'],['visa_status','Visa / residence status'],['notice_period','Notice period'],['salary_expectation','Salary expectation'],['weekly_hours','Weekly hours'],['relocation_willingness','Relocation willingness'],['travel_willingness','Travel willingness'],['consider_other_positions','Consider me for other positions (Yes/No)'],['drivers_license',"Driver's license"]].map(([key,label])=><Field label={label} key={key}><input type={key==='preferred_start_date'?'date':'text'} list={key==='availability'?'availability-presets':undefined} placeholder={key==='availability'?'For example: Immediately':undefined} value={text(profile[key as keyof CandidateProfile])} onChange={e=>update(key as keyof CandidateProfile,e.target.value)}/></Field>)}</div><datalist id="availability-presets"><option value="Immediately"/></datalist>
        </ProfileSection>
      </div></div>}
  </>
}

function ProfileSection({title,description,children}:{title:string;description:string;children:React.ReactNode}) { return <section className="panel form-section"><div className="section-intro"><h2>{title}</h2><p>{description}</p></div>{children}</section> }
