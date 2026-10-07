import { Heart, Eye, CalendarDays } from 'lucide-react';
import { PageHeader } from '../components/ui';
import { QueryUpload } from '../components/QueryUpload';
export function PetSearch() {
  return (
    <>
      <PageHeader
        eyebrow="HELP THEM FIND THEIR WAY HOME"
        title="Looking for a familiar face?"
        description="Search recent community sightings with a photo or a description of your pet."
      />
      <div className="pet-guidance">
        <span>
          <Heart size={18} /> Photos or words — either is a start
        </span>
        <span>
          <Eye size={18} /> Public sightings only
        </span>
        <span>
          <CalendarDays size={18} /> Choose your time window
        </span>
      </div>
      <QueryUpload pet />
    </>
  );
}
