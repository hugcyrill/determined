package resourcemanagers

import (
	"fmt"

	"github.com/determined-ai/determined/master/internal/config"
	"github.com/determined-ai/determined/master/internal/job"
	"github.com/determined-ai/determined/master/internal/sproto"
	"github.com/determined-ai/determined/master/pkg/actor"
	"github.com/determined-ai/determined/master/pkg/model"
)

const (
	fairShareScheduling  = "fair_share"
	priorityScheduling   = "priority"
	roundRobinScheduling = "round_robin"
)

// Scheduler schedules tasks on agents.  Its only function Schedule is called
// to determine which pending requests can be fulfilled and which scheduled tasks
// can be terminated. Schedule is expected to ba called every time there is a change
// to the cluster status, for example, new agents being connected, devices being disabled,
// and etc,. Schedule should avoid unnecessary shuffling tasks on agents to avoid
// the overhead of restarting a preempted task.
type Scheduler interface {
	Schedule(rp *ResourcePool) ([]*sproto.AllocateRequest, []*actor.Ref)
	JobQInfo(rp *ResourcePool) map[model.JobID]*job.RMJobInfo
}

// MakeScheduler returns the corresponding scheduler implementation.
func MakeScheduler(config *config.SchedulerConfig) Scheduler {
	switch config.GetType() {
	case priorityScheduling:
		return NewPriorityScheduler(config)
	case fairShareScheduling:
		return NewFairShareScheduler()
	case roundRobinScheduling:
		return NewRoundRobinScheduler()
	default:
		panic(fmt.Sprintf("invalid scheduler: %s", config.GetType()))
	}
}
