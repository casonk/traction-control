package broker

import (
	"time"

	"github.com/Azure/azure-sdk-for-go/sdk/azcore/runtime"
)

// azcoreFreq is how often long-running Azure operations are polled.
var azcoreFreq = runtime.PollUntilDoneOptions{Frequency: 5 * time.Second}
